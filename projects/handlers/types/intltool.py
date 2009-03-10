from projects.handlers.types import pot
from txcommon.log import logger


class IntltoolHandler(pot.POTHandler):
    """
    POTHandler abstraction layer, hense specific to the projects app.
    
    You can use this higher-level object to interact with a
    component's statistics that use intltool instead of meddling with 
    the lower-level POTManager. Each Component object gets one of these
    as ``component.trans``.
    
    """

    def set_stats(self):
        """
        Calculate stats for all translations of the component after 
        these tranlations be merged with a new POT file extracted 
        using intltool-update.    .
        """

        # Deleting all stats for the component
        logger.debug("Setting stats for %s" % self.component)
        self.tm.delete_stats_for_object(self.component)

        isIntltooled = self.tm.intltool_update()
        if not isIntltooled:
            logger.debug("intltool-update --pot has failed for %s" % 
                         self.component)
            isMsgmerged=False
        else:
            isMsgmerged=True

        # Set the source file (pot) to the database
        self.tm.set_source_stats(self.component, isMsgmerged)

        for lang in self.tm.get_langs():
            self.set_stats_for_lang(lang)

        # Cleaning the repository after running intltool-update
        self.component.unit.browser._clean_dir()
