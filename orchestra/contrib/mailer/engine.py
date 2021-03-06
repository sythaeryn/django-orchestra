import smtplib
from datetime import timedelta
from socket import error as SocketError

from django.core.mail import get_connection
from django.db.models import Q
from django.utils import timezone
from django.utils.encoding import smart_str

from orchestra.utils.sys import LockFile, OperationLocked

from . import settings
from .models import Message


def send_message(message, num=0, connection=None, bulk=settings.MAILER_BULK_MESSAGES):
    if num >= bulk and connection is not None:
        connection.close()
        connection = None
    if connection is None:
        # Reset connection with django
        num = 0
        connection = get_connection(backend='django.core.mail.backends.smtp.EmailBackend')
        connection.open()
    error = None
    try:
        connection.connection.sendmail(message.from_address, [message.to_address], smart_str(message.content))
    except (SocketError, smtplib.SMTPSenderRefused,
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPAuthenticationError) as err:
        message.defer()
        error = err
    else:
        message.sent()
    message.log(error)


def send_pending(bulk=settings.MAILER_BULK_MESSAGES):
    try:
        with LockFile('/dev/shm/mailer.send_pending.lock'):
            connection = None
            num = 0
            for message in Message.objects.filter(state=Message.QUEUED).order_by('priority'):
                send_message(message, num, connection, bulk)
                num += 1
            now = timezone.now()
            qs = Q()
            for retries, seconds in enumerate(settings.MAILER_DEFERE_SECONDS):
                delta = timedelta(seconds=seconds)
                qs = qs | Q(retries=retries, last_retry__lte=now-delta)
            for message in Message.objects.filter(state=Message.DEFERRED).filter(qs).order_by('priority'):
                send_message(message, num, connection, bulk)
            if connection is not None:
                connection.close()
    except OperationLocked:
        pass
