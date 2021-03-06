import io
import zipfile
from datetime import date

from django.contrib import messages
from django.contrib.admin import helpers
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.utils.safestring import mark_safe
from django.utils.translation import ungettext, ugettext_lazy as _

from orchestra.admin.forms import adminmodelformset_factory
from orchestra.admin.utils import get_object_from_url, change_url
from orchestra.utils.html import html_to_pdf

from .forms import SelectSourceForm
from .helpers import validate_contact


def download_bills(modeladmin, request, queryset):
    if queryset.count() > 1:
        bytesio = io.BytesIO()
        archive = zipfile.ZipFile(bytesio, 'w')
        for bill in queryset:
            pdf = bill.as_pdf()
            archive.writestr('%s.pdf' % bill.number, pdf)
        archive.close()
        response = HttpResponse(bytesio.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="orchestra-bills.zip"'
        return response
    bill = queryset.get()
    pdf = bill.as_pdf()
    return HttpResponse(pdf, content_type='application/pdf')
download_bills.verbose_name = _("Download")
download_bills.url_name = 'download'


def view_bill(modeladmin, request, queryset):
    bill = queryset.get()
    if not validate_contact(request, bill):
        return
    html = bill.html or bill.render()
    return HttpResponse(html)
view_bill.verbose_name = _("View")
view_bill.url_name = 'view'


@transaction.atomic
def close_bills(modeladmin, request, queryset):
    queryset = queryset.filter(is_open=True)
    if not queryset:
        messages.warning(request, _("Selected bills should be in open state"))
        return
    for bill in queryset:
        if not validate_contact(request, bill):
            return
    SelectSourceFormSet = adminmodelformset_factory(modeladmin, SelectSourceForm, extra=0)
    formset = SelectSourceFormSet(queryset=queryset)
    if request.POST.get('post') == 'generic_confirmation':
        formset = SelectSourceFormSet(request.POST, request.FILES, queryset=queryset)
        if formset.is_valid():
            transactions = []
            for form in formset.forms:
                source = form.cleaned_data['source']
                transaction = form.instance.close(payment=source)
                if transaction:
                    transactions.append(transaction)
            for bill in queryset:
                modeladmin.log_change(request, bill, 'Closed')
            messages.success(request, _("Selected bills have been closed"))
            if transactions:
                num = len(transactions)
                if num == 1:
                    url = change_url(transactions[0])
                else:
                    url = reverse('admin:payments_transaction_changelist')
                    url += 'id__in=%s' % ','.join([str(t.id) for t in transactions])
                context = {
                    'url': url,
                    'num': num,
                }
                message = ungettext(
                    _('<a href="%(url)s">One related transaction</a> has been created') % context,
                    _('<a href="%(url)s">%(num)i related transactions</a> have been created') % context,
                    num)
                messages.success(request, mark_safe(message))
            return
    opts = modeladmin.model._meta
    context = {
        'title': _("Are you sure about closing the following bills?"),
        'content_message': _("Once a bill is closed it can not be further modified.</p>"
                             "<p>Please select a payment source for the selected bills"),
        'action_name': 'Close bills',
        'action_value': 'close_bills',
        'display_objects': [],
        'queryset': queryset,
        'opts': opts,
        'app_label': opts.app_label,
        'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
        'formset': formset,
        'obj': get_object_from_url(modeladmin, request),
    }
    return render(request, 'admin/orchestra/generic_confirmation.html', context)
close_bills.verbose_name = _("Close")
close_bills.url_name = 'close'


def send_bills(modeladmin, request, queryset):
    for bill in queryset:
        if not validate_contact(request, bill):
            return
    num = 0
    for bill in queryset:
        bill.send()
        modeladmin.log_change(request, bill, 'Sent')
        num += 1
    messages.success(request, ungettext(
        _("One bill has been sent."),
        _("%i bills have been sent.") % num,
        num))
send_bills.verbose_name = lambda bill: _("Resend" if getattr(bill, 'is_sent', False) else "Send")
send_bills.url_name = 'send'


def manage_lines(modeladmin, request, queryset):
    url = reverse('admin:bills_bill_manage_lines')
    url += '?ids=%s' % ','.join(map(str, queryset.values_list('id', flat=True)))
    return redirect(url)


def undo_billing(modeladmin, request, queryset):
    group = {}
    for line in queryset.select_related('order'):
        if line.order_id:
            try:
                group[line.order].append(line)
            except KeyError:
                group[line.order] = [line]
    
    # Validate
    for order, lines in group.items():
        prev = None
        billed_on = date.max
        billed_until = date.max
        for line in sorted(lines, key=lambda l: l.start_on):
            if billed_on is not None:
                if line.order_billed_on is None:
                    billed_on = line.order_billed_on
                else:
                    billed_on = min(billed_on, line.order_billed_on)
            if billed_until is not None:
                if line.order_billed_until is None:
                    billed_until = line.order_billed_until
                else:
                    billed_until = min(billed_until, line.order_billed_until)
            if prev:
                if line.start_on != prev:
                    messages.error(request, "Line dates doesn't match.")
                    return
            else:
                # First iteration
                if order.billed_on < line.start_on:
                    messages.error(request, "Billed on is smaller than first line start_on.")
                    return
            prev = line.end_on
            nlines += 1
        if not prev:
            messages.error(request, "Order does not have lines!.")
        order.billed_until = billed_until
        order.billed_on = billed_on
    
    # Commit changes
    norders, nlines = 0, 0
    for order, lines in group.items():
        for line in lines:
            nlines += 1
            line.delete()
        # TODO update order history undo billing
        order.save(update_fields=('billed_until', 'billed_on'))
        norders += 1
    
    messages.success(request, _("%(norders)s orders and %(nlines)s lines undoed.") % {
        'nlines': nlines,
        'norders': norders
    })


def move_lines(modeladmin, request, queryset, action=None):
    # Validate
    target = request.GET.get('target')
    if not target:
        # select target
        context = {}
        return render(request, 'admin/orchestra/generic_confirmation.html', context)
    target = Bill.objects.get(pk=int(pk))
    if request.POST.get('post') == 'generic_confirmation':
        for line in queryset:
            line.bill = target
            line.save(update_fields=['bill'])
        # TODO bill history update
        messages.success(request, _("Lines moved"))
    # Final confirmation
    return render(request, 'admin/orchestra/generic_confirmation.html', context)


def copy_lines(modeladmin, request, queryset):
    # same as move, but changing action behaviour
    return move_lines(modeladmin, request, queryset)
