from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from . import arrivauk, intalink, mcgills, nctx, siri_sx, tfl_disruptions

bods_disruptions = db_periodic_task(crontab(minute="*/6"))(siri_sx.bods_disruptions)
tfl_disruptions_task = db_periodic_task(crontab(minute="*/6"))(
    tfl_disruptions.tfl_disruptions
)
nctx_disruptions_task = db_periodic_task(crontab(minute="*/6"))(nctx.nctx_disruptions)
intalink_disruptions_task = db_periodic_task(crontab(minute="*/6"))(
    intalink.intalink_disruptions
)
mcgills_disruptions_task = db_periodic_task(crontab(minute="*/6"))(
    mcgills.mcgills_disruptions
)
arrivauk_disruptions_task = db_periodic_task(crontab(minute="*/6"))(
    arrivauk.arrivauk_disruptions
)
