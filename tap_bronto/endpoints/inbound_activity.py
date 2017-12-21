from tap_bronto.schemas import get_field_selector, ACTIVITY_SCHEMA
from tap_bronto.state import incorporate, save_state, \
    get_last_record_value_for_table
from tap_bronto.stream import Stream

from datetime import datetime, timedelta
from dateutil import parser

import pytz
import singer
import suds

LOGGER = singer.get_logger()  # noqa


class InboundActivityStream(Stream):

    TABLE = 'inbound_activity'
    KEY_PROPERTIES = ['id']
    SCHEMA = ACTIVITY_SCHEMA

    def make_filter(self, start, end):
        _filter = self.client.factory.create(
            'recentInboundActivitySearchRequest')
        _filter.start = start
        _filter.end = end
        _filter.size = 5000
        _filter.readDirection = 'FIRST'

        return _filter

    def get_start_time(self):
        start = get_last_record_value_for_table(self.state,
                                                self.TABLE)

        earliest_available = datetime.now(pytz.utc) - timedelta(days=30)

        if start is None:
            start_string = self.config.get(
                'default_start_date',
                '2017-01-01T00:00:00-00:00')

            start = parser.parse(start_string)

        if earliest_available > start:
            LOGGER.warn('Start date before 30 days ago, but Bronto '
                        'only returns the past 30 days of activity. '
                        'Using a start date of -30 days.')
            return earliest_available

        return start - timedelta(days=3)

    def sync(self):
        key_properties = self.catalog.get('key_properties')
        table = self.TABLE

        singer.write_schema(
            self.catalog.get('stream'),
            self.catalog.get('schema'),
            key_properties=key_properties)

        start = self.get_start_time()
        end = start
        interval = timedelta(hours=1)

        LOGGER.info('Syncing inbound activities.')

        while end < datetime.now(pytz.utc):
            self.login()
            start = end
            end = start + interval
            LOGGER.info("Fetching activities from {} to {}".format(
                start, end))

            _filter = self.make_filter(start, end)
            field_selector = get_field_selector(
                self.catalog.get('schema'))

            hasMore = True

            while hasMore:
                try:
                    results = \
                        self.client.service.readRecentInboundActivities(
                            _filter)
                except suds.WebFault as e:
                    if '116' in e.fault.faultstring:
                        hasMore = False
                        break
                    else:
                        raise

                result_dicts = [suds.sudsobject.asdict(result)
                                for result in results]

                parsed_results = [field_selector(result)
                                  for result in result_dicts]

                singer.write_records(table, parsed_results)

                LOGGER.info('... {} results'.format(len(results)))

                _filter.readDirection = 'NEXT'

                if len(results) == 0:
                    hasMore = False

            self.state = incorporate(
                self.state, table, 'createdDate',
                start.replace(microsecond=0).isoformat())

            save_state(self.state)

        LOGGER.info('Done syncing inbound activities.')
