from django.contrib.admin import SimpleListFilter
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _


class BillTypeListFilter(SimpleListFilter):
    """ Filter tickets by created_by according to request.user """
    title = 'Type'
    parameter_name = ''
    
    def __init__(self, request, *args, **kwargs):
        super(BillTypeListFilter, self).__init__(request, *args, **kwargs)
        self.request = request
    
    def lookups(self, request, model_admin):
        return (
            ('bill', _("All")),
            ('invoice', _("Invoice")),
            ('amendmentinvoice', _("Amendment invoice")),
            ('fee', _("Fee")),
            ('fee', _("Amendment fee")),
            ('proforma', _("Pro-forma")),
        )
    
    def queryset(self, request, queryset):
        return queryset
    
    def value(self):
        return self.request.path.split('/')[-2]
    
    def choices(self, cl):
        for lookup, title in self.lookup_choices:
            yield {
                'selected': self.value() == lookup,
                'query_string': reverse('admin:bills_%s_changelist' % lookup),
                'display': title,
            }


class TotalListFilter(SimpleListFilter):
    title = _("total")
    parameter_name = 'total'
    
    def lookups(self, request, model_admin):
        return (
            ('gt', mark_safe("total &gt; 0")),
            ('eq', "total = 0"),
            ('lt', mark_safe("total &lt; 0")),
        )
    
    def queryset(self, request, queryset):
        if self.value() == 'gt':
            return queryset.filter(computed_total__gt=0)
        elif self.value() == 'eq':
            return queryset.filter(computed_total=0)
        elif self.value() == 'lt':
            return queryset.filter(computed_total__lt=0)
        return queryset


class HasBillContactListFilter(SimpleListFilter):
    """ Filter Nodes by group according to request.user """
    title = _("has bill contact")
    parameter_name = 'bill'
    
    def lookups(self, request, model_admin):
        return (
            ('True', _("Yes")),
            ('False', _("No")),
        )
    
    def queryset(self, request, queryset):
        if self.value() == 'True':
            return queryset.filter(billcontact__isnull=False)
        elif self.value() == 'False':
            return queryset.filter(billcontact__isnull=True)


class PaymentStateListFilter(SimpleListFilter):
    title = _("payment state")
    parameter_name = 'payment_state'
    
    def lookups(self, request, model_admin):
        return (
            ('OPEN', _("Open")),
            ('PAID', _("Paid")),
            ('PENDING', _("Pending")),
            ('BAD_DEBT', _("Bad debt")),
        )
    
    def queryset(self, request, queryset):
        Transaction = queryset.model.transactions.related.related_model
        if self.value() == 'OPEN':
            return queryset.filter(Q(is_open=True)|Q(type=queryset.model.PROFORMA))
        elif self.value() == 'PAID':
            zeros = queryset.filter(computed_total=0).values_list('id', flat=True)
            ammounts = Transaction.objects.exclude(bill_id__in=zeros).secured().group_by('bill_id')
            paid = []
            for bill_id, total in queryset.exclude(computed_total=0).values_list('id', 'computed_total'):
                try:
                    ammount = sum([t.ammount for t in ammounts[bill_id]])
                except KeyError:
                    pass
                else:
                    if abs(total) <= abs(ammount):
                        paid.append(bill_id)
            return queryset.filter(Q(computed_total=0)|Q(id__in=paid))
        elif self.value() == 'PENDING':
            has_transaction = queryset.exclude(transactions__isnull=True)
            non_rejected = has_transaction.exclude(transactions__state=Transaction.REJECTED)
            non_rejected = non_rejected.values_list('id', flat=True).distinct()
            return queryset.filter(pk__in=non_rejected)
        elif self.value() == 'BAD_DEBT':
            non_rejected = queryset.exclude(transactions__state=Transaction.REJECTED)
            non_rejected = non_rejected.values_list('id', flat=True).distinct()
            return queryset.exclude(pk__in=non_rejected)
