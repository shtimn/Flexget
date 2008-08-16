import logging

__pychecker__ = 'unusednames=parser,feed'

log = logging.getLogger("isohunt")

class ResolveIsoHunt:
    """IsoHunt resolver."""

    def register(self, manager, parser):
        manager.register('resolve_isohunt', group='resolver')

    def resolvable(self, feed, entry):
        url = entry['url']
        return url.startswith('http://isohunt.com') and url.find('download') == -1
        
    def resolve(self, feed, entry):
        entry['url'] = entry['url'].replace('torrent_details', 'download')
