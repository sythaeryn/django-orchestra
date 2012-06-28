from celery.task import task


#TODO: before doing things make sure that the deletion/deactivation is still active
@task(name="schedule_deletion")
def schedule_deletion_task(deletion_pk):
    from models import DeletionDate
    deletion = DeletionDate.objects.get(pk=deletion_pk)
    instance = deletion.content_object
    instance.delete()
    return "%s %s" % (instance, instance.__class__)


@task(name="schedule_deactivation")
def schedule_deactivation_task(deactivation_pk):
    from models import DeactivationPeriod
    deactivation = DeactivationPeriod.objects.get(pk=deactivation_pk)
    instance = deactivation.content_object
    from common.signals import service_updated
    service_updated.send(sender=instance.__class__, instance=instance)
    return "%s %s" % (instance, instance.__class__)
