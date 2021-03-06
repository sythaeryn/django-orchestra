import os
import textwrap

from django.utils.translation import ugettext_lazy as _

from orchestra.contrib.orchestration import ServiceController, replace
from orchestra.contrib.resources import ServiceMonitor

from . import settings


class UNIXUserBackend(ServiceController):
    """
    Basic UNIX system user/group support based on <tt>useradd</tt>, <tt>usermod</tt>, <tt>userdel</tt> and <tt>groupdel</tt>.
    Autodetects and uses ACL if available, for better permission management.
    """
    verbose_name = _("UNIX user")
    model = 'systemusers.SystemUser'
    actions = ('save', 'delete', 'set_permission', 'validate_path_exists')
    doc_settings = (settings, (
        'SYSTEMUSERS_DEFAULT_GROUP_MEMBERS',
        'SYSTEMUSERS_MOVE_ON_DELETE_PATH',
        'SYSTEMUSERS_FORBIDDEN_PATHS'
    ))
    
    def save(self, user):
        context = self.get_context(user)
        if not context['user']:
            return
        groups = ','.join(self.get_groups(user))
        context['groups_arg'] = '--groups %s' % groups if groups else ''
        # TODO userd add will fail if %(user)s group already exists
        self.append(textwrap.dedent("""
            # Update/create user state for %(user)s
            if [[ $( id %(user)s ) ]]; then
                usermod %(user)s --home %(home)s \\
                    --password '%(password)s' \\
                    --shell %(shell)s %(groups_arg)s
            else
                useradd_code=0
                useradd %(user)s --home %(home)s \\
                    --password '%(password)s' \\
                    --shell %(shell)s %(groups_arg)s || useradd_code=$?
                if [[ $useradd_code -eq 8 ]]; then
                    # User is logged in, kill and retry
                    pkill -u %(user)s; sleep 2
                    pkill -9 -u %(user)s; sleep 1
                    useradd %(user)s --home %(home)s \\
                        --password '%(password)s' \\
                        --shell %(shell)s %(groups_arg)s
                elif [[ $useradd_code -ne 0 ]]; then
                    exit $useradd_code
                fi
            fi
            mkdir -p %(base_home)s
            chmod 750 %(base_home)s
            chown %(user)s:%(user)s %(base_home)s""") % context
        )
        if context['home'] != context['base_home']:
            self.append(textwrap.dedent("""
                # Set extra permissions: %(user)s home is inside %(mainuser)s home
                if [[ $(mount | grep "^$(df %(home)s|grep '^/')\s" | grep acl) ]]; then
                    # Accountn group as the owner
                    chown %(mainuser)s:%(mainuser)s %(home)s
                    chmod g+s %(home)s
                    # Home access
                    setfacl -m u:%(user)s:--x '%(mainuser_home)s'
                    # Grant perms to future files within the directory
                    setfacl -m d:u:%(user)s:rwx %(home)s
                    # Grant access to main user
                    setfacl -m d:u:%(mainuser)s:rwx %(home)s
                else
                    chmod g+rxw %(home)s
                    chown %(user)s:%(user)s %(home)s
                fi""") % context
            )
        for member in settings.SYSTEMUSERS_DEFAULT_GROUP_MEMBERS:
            context['member'] = member
            self.append('usermod -a -G %(user)s %(member)s || exit_code=$?' % context)
        if not user.is_main:
            self.append('usermod -a -G %(user)s %(mainuser)s || exit_code=$?' % context)
    
    def delete(self, user):
        context = self.get_context(user)
        if not context['user']:
            return
        self.append(textwrap.dedent("""
            # Delete %(user)s user
            nohup bash -c 'sleep 2 && killall -u %(user)s -s KILL' &> /dev/null &
            killall -u %(user)s || true
            userdel %(user)s || exit_code=$?
            groupdel %(group)s || exit_code=$?\
            """) % context
        )
        if context['deleted_home']:
            self.append("mv %(base_home)s %(deleted_home)s || exit_code=$?" % context)
        else:
            self.append("rm -fr %(base_home)s" % context)
    
    def set_permission(self, user):
        context = self.get_context(user)
        context.update({
            'perm_action': user.set_perm_action,
            'perm_home': user.set_perm_base_home,
            'perm_to': os.path.join(user.set_perm_base_home, user.set_perm_home_extension),
        })
        exclude_acl = []
        for exclude in settings.SYSTEMUSERS_FORBIDDEN_PATHS:
            context['exclude_acl'] = os.path.join(context['perm_home'], exclude)
            exclude_acl.append('-not -path "%(exclude_acl)s"' % context)
        context['exclude_acl'] = ' \\\n    -a '.join(exclude_acl) if exclude_acl else ''
        if user.set_perm_perms == 'rw':
            context['perm_perms'] = 'rwx' if user.set_perm_action == 'grant' else '---'
        elif user.set_perm_perms == 'r':
            context['perm_perms'] = 'r-x' if user.set_perm_action == 'grant' else '-wx'
        elif user.set_perm_perms == 'w':
            context['perm_perms'] = '-wx' if user.set_perm_action == 'grant' else 'r-x'
        if user.set_perm_action == 'grant':
            self.append(textwrap.dedent("""\
                # Home access
                setfacl -m u:%(user)s:--x '%(perm_home)s'
                # Grant perms to existing and future files
                find '%(perm_to)s' %(exclude_acl)s \\
                    -exec setfacl -m u:%(user)s:%(perm_perms)s {} \\;
                find '%(perm_to)s' -type d %(exclude_acl)s \\
                    -exec setfacl -m d:u:%(user)s:%(perm_perms)s {} \\;
                # Account group as the owner of new files
                chmod g+s '%(perm_to)s'""") % context
            )
            if not user.is_main:
                self.append(textwrap.dedent("""\
                    # Grant access to main user
                    find '%(perm_to)s' -type d %(exclude_acl)s \\
                        -exec setfacl -m d:u:%(mainuser)s:rwx {} \\;\
                    """) % context
                )
        elif user.set_perm_action == 'revoke':
            self.append(textwrap.dedent("""\
                # Revoke permissions
                find '%(perm_to)s' %(exclude_acl)s \\
                    -exec setfacl -m u:%(user)s:%(perm_perms)s {} \\;\
                """) % context
            )
        else:
            raise NotImplementedError()
    
    def validate_path_exists(self, user):
        context = {
            'path': user.path_to_validate,
        }
        self.append(textwrap.dedent("""
            if [[ ! -e '%(path)s' ]]; then
                echo "%(path)s path does not exists." >&2
            fi""") % context
        )
    
    def get_groups(self, user):
        if user.is_main:
            return user.account.systemusers.exclude(username=user.username).values_list('username', flat=True)
        return list(user.groups.values_list('username', flat=True))
    
    def get_context(self, user):
        context = {
            'object_id': user.pk,
            'user': user.username,
            'group': user.username,
            'password': user.password if user.active else '*%s' % user.password,
            'shell': user.shell,
            'mainuser': user.username if user.is_main else user.account.username,
            'home': user.get_home(),
            'base_home': user.get_base_home(),
            'mainuser_home': user.main.get_home(),
        }
        context['deleted_home'] = settings.SYSTEMUSERS_MOVE_ON_DELETE_PATH % context
        return replace(context, "'", '"')


class UNIXUserDisk(ServiceMonitor):
    """
    <tt>du -bs &lt;home&gt;</tt>
    """
    model = 'systemusers.SystemUser'
    resource = ServiceMonitor.DISK
    verbose_name = _('UNIX user disk')
    
    def prepare(self):
        super(UNIXUserDisk, self).prepare()
        self.append(textwrap.dedent("""\
            function monitor () {
                { du -bs "$1" || echo 0; } | awk {'print $1'}
            }"""
        ))
    
    def monitor(self, user):
        context = self.get_context(user)
        self.append("echo %(object_id)s $(monitor %(base_home)s)" % context)
    
    def get_context(self, user):
        context = {
            'object_id': user.pk,
            'base_home': user.get_base_home(),
        }
        return replace(context, "'", '"')


class Exim4Traffic(ServiceMonitor):
    """
    Exim4 mainlog parser for mails sent on the webserver by system users (e.g. via PHP <tt>mail()</tt>)
    """
    model = 'systemusers.SystemUser'
    resource = ServiceMonitor.TRAFFIC
    verbose_name = _("Exim4 traffic")
    script_executable = '/usr/bin/python'
    doc_settings = (settings,
        ('SYSTEMUSERS_MAIL_LOG_PATH',)
    )
    
    def prepare(self):
        mainlog = settings.SYSTEMUSERS_MAIL_LOG_PATH
        context = {
            'current_date': self.current_date.strftime("%Y-%m-%d %H:%M:%S %Z"),
            'mainlogs': str((mainlog, mainlog+'.1')),
        }
        self.append(textwrap.dedent("""\
            import re
            import sys
            from datetime import datetime
            from dateutil import tz
            
            def to_local_timezone(date, tzlocal=tz.tzlocal()):
                date = datetime.strptime(date, '%Y-%m-%d %H:%M:%S %Z')
                date = date.replace(tzinfo=tz.tzutc())
                date = date.astimezone(tzlocal)
                return date
            
            mainlogs = {mainlogs}
            # Use local timezone
            end_date = to_local_timezone('{current_date}')
            end_date = int(end_date.strftime('%Y%m%d%H%M%S'))
            users = {{}}
            
            def prepare(object_id, username, ini_date):
                global users
                ini_date = to_local_timezone(ini_date)
                ini_date = int(ini_date.strftime('%Y%m%d%H%M%S'))
                users[username] = [ini_date, object_id, 0]
            
            def monitor(users, end_date, mainlogs):
                user_regex = re.compile(r' U=([^ ]+) ')
                for mainlog in mainlogs:
                    try:
                        with open(mainlog, 'r') as mainlog:
                            for line in mainlog.readlines():
                                if ' <= ' in line and 'P=local' in line:
                                    username = user_regex.search(line).groups()[0]
                                    try:
                                        sender = users[username]
                                    except KeyError:
                                        continue
                                    else:
                                        date, time, id, __, __, user, protocol, size = line.split()[:8]
                                        date = date.replace('-', '')
                                        date += time.replace(':', '')
                                        if sender[0] < int(date) < end_date:
                                            sender[2] += int(size[2:])
                    except IOError as e:
                        sys.stderr.write(e)
                
                for username, opts in users.iteritems():
                    __, object_id, size = opts
                    print object_id, size
            """).format(**context)
        )
    
    def commit(self):
        self.append('monitor(users, end_date, mainlogs)')
    
    def monitor(self, user):
        context = self.get_context(user)
        self.append("prepare(%(object_id)s, '%(username)s', '%(last_date)s')" % context)
    
    def get_context(self, user):
        context = {
            'username': user.username,
            'object_id': user.pk,
            'last_date': self.get_last_date(user.pk).strftime("%Y-%m-%d %H:%M:%S %Z"),
        }
        return context


class VsFTPdTraffic(ServiceMonitor):
    """
    vsFTPd log parser.
    """
    model = 'systemusers.SystemUser'
    resource = ServiceMonitor.TRAFFIC
    verbose_name = _('VsFTPd traffic')
    script_executable = '/usr/bin/python'
    doc_settings = (settings,
        ('SYSTEMUSERS_FTP_LOG_PATH',)
    )
    
    def prepare(self):
        vsftplog = settings.SYSTEMUSERS_FTP_LOG_PATH
        context = {
            'current_date': self.current_date.strftime("%Y-%m-%d %H:%M:%S %Z"),
            'vsftplogs': str((vsftplog, vsftplog+'.1')),
        }
        self.append(textwrap.dedent("""\
            import re
            import sys
            from datetime import datetime
            from dateutil import tz
            
            def to_local_timezone(date, tzlocal=tz.tzlocal()):
                date = datetime.strptime(date, '%Y-%m-%d %H:%M:%S %Z')
                date = date.replace(tzinfo=tz.tzutc())
                date = date.astimezone(tzlocal)
                return date
            
            vsftplogs = {vsftplogs}
            # Use local timezone
            end_date = to_local_timezone('{current_date}')
            end_date = int(end_date.strftime('%Y%m%d%H%M%S'))
            users = {{}}
            months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
            months = dict((m, '%02d' % n) for n, m in enumerate(months, 1))
            
            def prepare(object_id, username, ini_date):
                global users
                ini_date = to_local_timezone(ini_date)
                ini_date = int(ini_date.strftime('%Y%m%d%H%M%S'))
                users[username] = [ini_date, object_id, 0]
            
            def monitor(users, end_date, months, vsftplogs):
                user_regex = re.compile(r'\] \[([^ ]+)\] (OK|FAIL) ')
                bytes_regex = re.compile(r', ([0-9]+) bytes, ')
                for vsftplog in vsftplogs:
                    try:
                        with open(vsftplog, 'r') as vsftplog:
                            for line in vsftplog.readlines():
                                if ' bytes, ' in line:
                                    username = user_regex.search(line).groups()[0]
                                    try:
                                        user = users[username]
                                    except KeyError:
                                        continue
                                    else:
                                        __, month, day, time, year = line.split()[:5]
                                        date = year + months[month] + day + time.replace(':', '')
                                        if user[0] < int(date) < end_date:
                                            bytes = bytes_regex.search(line).groups()[0]
                                            user[2] += int(bytes)
                    except IOError as e:
                        sys.stderr.write(e)
                
                for username, opts in users.items():
                    __, object_id, size = opts
                    print object_id, size
            """).format(**context)
        )
    
    def monitor(self, user):
        context = self.get_context(user)
        self.append("prepare(%(object_id)s, '%(username)s', '%(last_date)s')" % context)
    
    def commit(self):
        self.append('monitor(users, end_date, months, vsftplogs)')
    
    def get_context(self, user):
        context = {
            'last_date': self.get_last_date(user.pk).strftime("%Y-%m-%d %H:%M:%S %Z"),
            'object_id': user.pk,
            'username': user.username,
        }
        return replace(context, "'", '"')
