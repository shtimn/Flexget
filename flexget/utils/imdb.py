from __future__ import unicode_literals, division, absolute_import
import difflib
import logging
import re
from flexget.utils.soup import get_soup
from flexget.utils.requests import Session
from flexget.utils.tools import str_to_int
from BeautifulSoup import Tag

log = logging.getLogger('utils.imdb')
# IMDb delivers a version of the page which is unparsable to unknown (and some known) user agents, such as requests'
# Spoof the old urllib user agent to keep results consistent
requests = Session()
requests.headers.update({'User-Agent': 'Python-urllib/2.6'})
# give imdb a little break between requests (see: http://flexget.com/ticket/129#comment:1)
requests.set_domain_delay('imdb.com', '3 seconds')


def is_imdb_url(url):
    """Tests the url to see if it's for imdb.com."""
    if not isinstance(url, basestring):
        return
    # Probably should use urlparse.
    return re.match(r'https?://[^/]*imdb\.com/', url)


def extract_id(url):
    """Return IMDb ID of the given URL. Return None if not valid or if URL is not a string."""
    if not isinstance(url, basestring):
        return
    m = re.search(r'((?:nm|tt)[\d]{7})', url)
    if m:
        return m.group(1)


def make_url(imdb_id):
    """Return IMDb URL of the given ID"""
    return u'http://www.imdb.com/title/%s/' % imdb_id


class ImdbSearch(object):

    def __init__(self):
        # de-prioritize aka matches a bit
        self.aka_weight = 0.95
        # prioritize first
        self.first_weight = 1.1
        self.min_match = 0.5
        self.min_diff = 0.01
        self.debug = False

        self.max_results = 10

    def ireplace(self, str, old, new, count=0):
        """Case insensitive string replace"""
        pattern = re.compile(re.escape(old), re.I)
        return re.sub(pattern, new, str, count)

    def smart_match(self, raw_name):
        """Accepts messy name, cleans it and uses information available to make smartest and best match"""
        from flexget.utils.titles.movie import MovieParser
        parser = MovieParser()
        parser.data = raw_name
        parser.parse()
        name = parser.name
        year = parser.year
        if name == '':
            log.critical('Failed to parse name from %s' % raw_name)
            return None
        log.debug('smart_match name=%s year=%s' % (name, str(year)))
        return self.best_match(name, year)

    def best_match(self, name, year=None):
        """Return single movie that best matches name criteria or None"""
        movies = self.search(name)

        if not movies:
            log.debug('search did not return any movies')
            return None

        # remove all movies below min_match, and different year
        for movie in movies[:]:
            if year and movie.get('year'):
                if movie['year'] != str(year):
                    log.debug('best_match removing %s - %s (wrong year: %s)' % (movie['name'], movie['url'], str(movie['year'])))
                    movies.remove(movie)
                    continue
            if movie['match'] < self.min_match:
                log.debug('best_match removing %s (min_match)' % movie['name'])
                movies.remove(movie)
                continue

        if not movies:
            log.debug('FAILURE: no movies remain')
            return None

        # if only one remains ..
        if len(movies) == 1:
            log.debug('SUCCESS: only one movie remains')
            return movies[0]

        # check min difference between best two hits
        diff = movies[0]['match'] - movies[1]['match']
        if diff < self.min_diff:
            log.debug('unable to determine correct movie, min_diff too small (`%s` <-?-> `%s`)' %
                (movies[0], movies[1]))
            for m in movies:
                log.debug('remain: %s (match: %s) %s' % (m['name'], m['match'], m['url']))
            return None
        else:
            return movies[0]

    def search(self, name):
        """Return array of movie details (dict)"""
        log.debug('Searching: %s' % name)
        url = u'http://www.imdb.com/find'
        # This will only include movies searched by title in the results
        params = {'q': name, 's': 'tt', 'ttype': 'ft'}

        log.debug('Serch query: %s' % repr(url))
        page = requests.get(url, params=params)
        actual_url = page.url

        movies = []
        # in case we got redirected to movie page (perfect match)
        re_m = re.match(r'.*\.imdb\.com/title/tt\d+/', actual_url)
        if re_m:
            actual_url = re_m.group(0)
            log.debug('Perfect hit. Search got redirected to %s' % actual_url)
            movie = {}
            movie['match'] = 1.0
            movie['name'] = name
            movie['url'] = actual_url
            movie['imdb_id'] = extract_id(actual_url)
            movie['year'] = None # skips year check
            movies.append(movie)
            return movies

        # the god damn page has declared a wrong encoding
        soup = get_soup(page.content)

        section_table = soup.find('table', 'findList')
        if not section_table:
            log.debug('results table not found')
            return

        rows = section_table.find_all('td', 'result_text')
        if not rows:
            log.debug('Titles section does not have links')
        for count, row in enumerate(rows):
            # Title search gives a lot of results, only check the first ones
            if count > self.max_results:
                break

            movie = {}
            additional = re.findall(r'\((.*?)\)', row.text)
            if len(additional) > 0:
                movie['year'] = additional[-1]

            link = row.find_next('a')
            movie['name'] = link.text
            movie['url'] = 'http://www.imdb.com' + link.get('href')
            movie['imdb_id'] = extract_id(movie['url'])
            log.debug('processing name: %s url: %s' % (movie['name'], movie['url']))

            # calc & set best matching ratio
            seq = difflib.SequenceMatcher(lambda x: x == ' ', movie['name'].title(), name.title())
            ratio = seq.ratio()

            # check if some of the akas have better ratio
            for aka in link.parent.find_all('p', attrs={'class': 'find-aka'}):
                aka = aka.next.string
                match = re.search(r'".*"', aka)
                if not match:
                    log.debug('aka `%s` is invalid' % aka)
                    continue
                aka = match.group(0).replace('"', '')
                log.trace('processing aka %s' % aka)
                seq = difflib.SequenceMatcher(lambda x: x == ' ', aka.title(), name.title())
                aka_ratio = seq.ratio()
                if aka_ratio > ratio:
                    ratio = aka_ratio * self.aka_weight
                    log.debug('- aka `%s` matches better to `%s` ratio %s (weighted to %s)' %
                              (aka, name, aka_ratio, ratio))

            # prioritize items by position
            position_ratio = (self.first_weight - 1) / (count + 1) + 1
            log.debug('- prioritizing based on position %s `%s`: %s' % (count, movie['url'], position_ratio))
            ratio *= position_ratio

            # store ratio
            movie['match'] = ratio
            movies.append(movie)

        movies.sort(key=lambda x: x['match'], reverse=True)
        return movies


class ImdbParser(object):
    """Quick-hack to parse relevant imdb details"""

    def __init__(self):
        self.genres = []
        self.languages = []
        self.actors = {}
        self.directors = {}
        self.score = 0.0
        self.votes = 0
        self.year = 0
        self.plot_outline = None
        self.name = None
        self.url = None
        self.imdb_id = None
        self.photo = None
        self.mpaa_rating = ''

    def __str__(self):
        return '<ImdbParser(name=%s,imdb_id=%s)>' % (self.name, self.imdb_id)

    def parse(self, imdb_id):
        self.imdb_id = extract_id(imdb_id)
        url = make_url(self.imdb_id)
        self.url = url
        page = requests.get(url)
        soup = get_soup(page.content)

        # get photo
        tag_photo = soup.find('div', attrs={'class': 'photo'})
        if tag_photo:
            tag_img = tag_photo.find('img')
            if tag_img:
                self.photo = tag_img.get('src')
                log.debug('Detected photo: %s' % self.photo)

        # get rating. Always the first absmiddle.
        tag_infobar_div = soup.find('div', attrs={'class': 'infobar'})
        if tag_infobar_div:
            tag_mpaa_rating = tag_infobar_div.find('img', attrs={'class': 'absmiddle'})
            if tag_mpaa_rating:
                if tag_mpaa_rating['alt'] != tag_mpaa_rating['title']:
                    # If we've found something of class absmiddle in the infobar,
                    # it should be mpaa_rating, since that's the only one in there.
                    log.warning("MPAA rating alt and title don't match for URL %s - plugin needs an update?" % url)
                else:
                    self.mpaa_rating = tag_mpaa_rating['alt']
                    log.debug('Detected mpaa rating: %s' % self.mpaa_rating)
            else:
                log.debug('Unable to match signature of mpaa rating for %s - could be a TV episode, or plugin needs update?' % url)
        else:
            # We should match the infobar, it's an integral part of the IMDB page.
            log.warning('Unable to get infodiv class for %s - plugin needs update?' % url)

        # get name
        tag_name = soup.find('h1')
        if tag_name:
            if tag_name.next:
                # Handle a page not found in IMDB. tag_name.string is
                # "<br/> Page Not Found" and there is no next tag. Thus, None.
                if tag_name.next.string is not None:
                    self.name = tag_name.next.string.strip()
                    log.debug('Detected name: %s' % self.name)
        else:
            log.warning('Unable to get name for %s - plugin needs update?' % url)

        # detect if movie is eligible for ratings
        rating_ineligible = soup.find('div', attrs={'class': 'rating-ineligible'})
        if rating_ineligible:
            log.debug('movie is not eligible for ratings')
        else:
            # get votes
            tag_votes = soup.find(itemprop='ratingCount')
            if tag_votes:
                self.votes = str_to_int(tag_votes.string) or 0
                log.debug('Detected votes: %s' % self.votes)
            else:
                log.warning('Unable to get votes for %s - plugin needs update?' % url)

            # get score
            span_score = soup.find(itemprop='ratingValue')
            if span_score:
                try:
                    self.score = float(span_score.string)
                except ValueError:
                    log.debug('tag_score %s is not valid float' % span_score.text)
                log.debug('Detected score: %s' % self.score)
            else:
                log.warning('Unable to get score for %s - plugin needs update?' % url)

        # get genres
        for link in soup.find_all('a', attrs={'itemprop': 'genre'}):
            self.genres.append(link.text.lower())

        # get languages
        for link in soup.find_all('a', attrs={'itemprop': 'inLanguage'}):
            # skip non-primary languages "(a few words)", etc.
            m = re.search('(?x) \( [^()]* \\b few \\b', link.next_sibling)
            if not m:
                lang = link.text.lower()
                if not lang in self.languages:
                    self.languages.append(lang.strip())

        # get year
        tag_year = soup.find('a', attrs={'href': re.compile('^/year/\d+')})
        if tag_year:
            self.year = int(tag_year.text)
            log.debug('Detected year: %s' % self.year)
        elif soup.head.title:
            m = re.search(r'(\d{4})\)', soup.head.title.string)
            if m:
                self.year = int(m.group(1))
                log.debug('Detected year: %s' % self.year)
            else:
                log.warning('Unable to get year for %s (regexp mismatch) - plugin needs update?' % url)
        else:
            log.warning('Unable to get year for %s (missing title) - plugin needs update?' % url)

        # get main cast
        tag_cast = soup.find('table', 'cast_list')
        if tag_cast:
            for actor in tag_cast.find_all('a', href=re.compile('/name/nm')):
                actor_id = extract_id(actor['href'])
                actor_name = actor.text
                # tag instead of name
                if isinstance(actor_name, Tag):
                    actor_name = None
                self.actors[actor_id] = actor_name

        # get director(s)
        h4_director = soup.find('h4', text=re.compile('Director'))
        if h4_director:
            for director in h4_director.parent.parent.find_all('a', href=re.compile('/name/nm')):
                director_id = extract_id(director['href'])
                director_name = director.text
                # tag instead of name
                if isinstance(director_name, Tag):
                    director_name = None
                self.directors[director_id] = director_name

        log.debug('Detected genres: %s' % self.genres)
        log.debug('Detected languages: %s' % self.languages)
        log.debug('Detected director(s): %s' % ', '.join(self.directors))
        log.debug('Detected actors: %s' % ', '.join(self.actors))

        # get plot
        h2_plot = soup.find('h2', text='Storyline')
        if h2_plot:
            p_plot = h2_plot.find_next('p')
            if p_plot:
                self.plot_outline = p_plot.next.string.strip()
                log.debug('Detected plot outline: %s' % self.plot_outline)
            else:
                log.debug('Plot does not have p-tag')
        else:
            log.debug('Failed to find plot')
