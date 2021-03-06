from __future__ import unicode_literals, division, absolute_import
import hashlib
import logging
import re
from requests import RequestException
from flexget.utils import json
from flexget.utils.cached_input import cached
from flexget.plugin import register_plugin, PluginError
from flexget.entry import Entry

log = logging.getLogger('trakt_list')


class TraktList(object):
    """Creates an entry for each item in your trakt list.

    Syntax:

    trakt_list:
      username: <value>
      api_key: <value>
      strip_dates: <yes|no>
      movies: <all|loved|hated|collection|watchlist>
      series: <all|loved|hated|collection|watchlist|watched>
      custom: <value>

    Options username and api_key are required.
    """

    movie_map = {
        'title': 'title',
        'url': 'url',
        'imdb_id': 'imdb_id',
        'tmdb_id': 'tmdb_id',
        # Generic fields filled by all movie lookup plugins:
        'movie_name': 'title',
        'movie_year': 'year'}

    series_map = {
        'title': 'title',
        'url': 'url',
        'imdb_id': 'imdb_id',
        'thetvdb_id': 'tvdb_id',
        'tvrage_id': 'tvrage_id'}

    def validator(self):
        from flexget import validator
        root = validator.factory('dict')
        root.accept('text', key='username', requried=True)
        root.accept('text', key='api_key', required=True)
        root.accept('text', key='password')
        root.accept('choice', key='movies').accept_choices(['all', 'loved', 'hated', 'collection', 'watchlist'])
        root.accept('choice', key='series').accept_choices(['all', 'loved', 'hated', 'collection', 'watched', 'watchlist'])
        root.accept('text', key='custom')
        root.accept('boolean', key='strip_dates')
        return root

    @cached('trakt_list', persist='2 hours')
    def on_task_input(self, task, config):
        # Don't edit the config, or it won't pass validation on rerun
        url_params = config.copy()
        if 'movies' in config and 'series' in config:
            raise PluginError('Cannot use both series list and movies list in the same task.')
        if 'movies' in config:
            url_params['data_type'] = 'movies'
            url_params['list_type'] = config['movies']
            map = self.movie_map
        elif 'series' in config:
            url_params['data_type'] = 'shows'
            url_params['list_type'] = config['series']
            map = self.series_map
        elif 'custom' in config:
            url_params['data_type'] = 'custom'
            # Do some translation from visible list name to prepare for use in url
            list_name = config['custom'].lower()
            # These characters are just stripped in the url
            for char in '!@#$%^*()[]{}/=?+\\|-_':
                list_name = list_name.replace(char, '')
            # These characters get replaced
            list_name = list_name.replace('&', 'and')
            list_name = list_name.replace(' ', '-')
            url_params['list_type'] = list_name
            # Map type is per item in custom lists
        else:
            raise PluginError('Must define movie or series lists to retrieve from trakt.')

        url = 'http://api.trakt.tv/user/'
        auth = None
        if url_params['data_type'] == 'custom':
            url += 'list.json/%(api_key)s/%(username)s/%(list_type)s'
        elif url_params['list_type'] == 'watchlist':
            url += 'watchlist/%(data_type)s.json/%(api_key)s/%(username)s'
        else:
            url += 'library/%(data_type)s/%(list_type)s.json/%(api_key)s/%(username)s'
        url = url % url_params

        if 'password' in config:
            auth = {'username': config['username'],
                    'password': hashlib.sha1(config['password']).hexdigest()}

        entries = []
        log.verbose('Retrieving list %s %s...' % (url_params['data_type'], url_params['list_type']))

        result = task.requests.get(url, data=json.dumps(auth))
        try:
            data = task.requests.post(url, data=json.dumps(auth)).json()
        except RequestException as e:
            raise PluginError('Could not retrieve list from trakt (%s)' % e.message)

        def check_auth():
            if task.requests.post(
                    'http://api.trakt.tv/account/test/' + config['api_key'],
                    data=json.dumps(auth), raise_status=False
                    ).status_code != 200:
                raise PluginError('Authentication to trakt failed.')

        if 'error' in data:
            check_auth()
            raise PluginError('Error getting trakt list: %s' % data['error'])
        if not data:
            check_auth()
            log.warning('No data returned from trakt.')
            return
        if url_params['data_type'] == 'custom':
            if not isinstance(data['items'], list):
                raise PluginError('Faulty custom items in response: %s' % data['items'])
            data = data['items']
        for item in data:
            if url_params['data_type'] == 'custom':
                if item['type'] == 'movie':
                    map = self.movie_map
                    item = item['movie']
                else:
                    map = self.series_map
                    item = item['show']
            entry = Entry()
            entry.update_using_map(map, item)
            if entry.isvalid():
                if config.get('strip_dates'):
                    # Remove year from end of name if present
                    entry['title'] = re.sub('\s+\(\d{4}\)$', '', entry['title'])
                entries.append(entry)

        return entries


register_plugin(TraktList, 'trakt_list', api_ver=2)
