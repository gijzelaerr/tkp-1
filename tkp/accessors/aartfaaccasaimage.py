import logging
from tkp.accessors import CasaImage

logger = logging.getLogger(__name__)


class AartfaacCasaImage(CasaImage):

    def __init__(self, url, plane=0, beam=None):
        super(AartfaacCasaImage, self).__init__(url, plane=0, beam=None)
        # TODO: AARTFAAC header doesn't contain this (yet)
        self.taustart_ts = self.parse_taustartts()
        self.tau_time = 1
        self.telescope = self.table.getkeyword('coords')['telescope']


    def parse_frequency(self):
        """
        Extract frequency related information from headers

        (Overrides the implementation in CasaImage, which pulls the entries
        from the 'spectral2' sub-table.)

        """
        keywords = self.table.getkeywords()
        freq_eff = keywords['coords']['spectral1']['restfreq']
        freq_bw = keywords['coords']['spectral1']['wcs']['cdelt']
        return freq_eff, freq_bw



