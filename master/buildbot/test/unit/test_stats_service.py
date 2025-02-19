# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members


import mock

from twisted.internet import defer
from twisted.trial import unittest

from buildbot import config
from buildbot.errors import CaptureCallbackError
from buildbot.statistics import capture
from buildbot.statistics import stats_service
from buildbot.statistics import storage_backends
from buildbot.statistics.storage_backends.base import StatsStorageBase
from buildbot.statistics.storage_backends.influxdb_client import InfluxStorageService
from buildbot.test import fakedb
from buildbot.test.fake import fakemaster
from buildbot.test.fake import fakestats
from buildbot.test.reactor import TestReactorMixin
from buildbot.test.steps import TestBuildStepMixin
from buildbot.test.util import logging


class TestStatsServicesBase(TestReactorMixin, unittest.TestCase):

    BUILDER_NAMES = ['builder1', 'builder2']
    BUILDER_IDS = [1, 2]

    @defer.inlineCallbacks
    def setUp(self):
        self.setup_test_reactor()
        self.master = fakemaster.make_master(self, wantMq=True, wantData=True,
                                             wantDb=True)

        for builderid, name in zip(self.BUILDER_IDS, self.BUILDER_NAMES):
            self.master.db.builders.addTestBuilder(
                builderid=builderid, name=name)

        self.stats_service = stats_service.StatsService(storage_backends=[
                                                            fakestats.FakeStatsStorageService()
                                                        ],
                                                        name="FakeStatsService")
        yield self.stats_service.setServiceParent(self.master)
        yield self.master.startService()

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.master.stopService()


class TestStatsServicesConfiguration(TestStatsServicesBase):

    @defer.inlineCallbacks
    def test_reconfig_with_no_storage_backends(self):
        new_storage_backends = []
        yield self.stats_service.reconfigService(new_storage_backends)
        self.checkEqual(new_storage_backends)

    @defer.inlineCallbacks
    def test_reconfig_with_fake_storage_backend(self):
        new_storage_backends = [
            fakestats.FakeStatsStorageService(name='One'),
            fakestats.FakeStatsStorageService(name='Two')
        ]
        yield self.stats_service.reconfigService(new_storage_backends)
        self.checkEqual(new_storage_backends)

    @defer.inlineCallbacks
    def test_reconfig_with_consumers(self):
        backend = fakestats.FakeStatsStorageService(name='One')
        backend.captures = [capture.CaptureProperty('test_builder', 'test')]
        new_storage_backends = [backend]

        yield self.stats_service.reconfigService(new_storage_backends)
        yield self.stats_service.reconfigService(new_storage_backends)
        self.assertEqual(len(self.master.mq.qrefs), 1)

    @defer.inlineCallbacks
    def test_bad_configuration(self):
        # Reconfigure with a bad configuration.
        new_storage_backends = [mock.Mock()]
        with self.assertRaises(TypeError):
            yield self.stats_service.reconfigService(new_storage_backends)

    def checkEqual(self, new_storage_backends):
        # Check whether the new_storage_backends was set in reconfigService
        registeredStorageServices = \
            [s for s in self.stats_service.registeredStorageServices
             if isinstance(s, StatsStorageBase)]
        for s in new_storage_backends:
            if s not in registeredStorageServices:
                raise AssertionError("reconfigService failed."
                                     "Not all storage services registered.")


class TestInfluxDB(TestStatsServicesBase, logging.LoggingMixin):
    # Smooth test of influx db service. We don't want to force people to install influxdb, so we
    # just disable this unit test if the influxdb module is not installed,
    # using SkipTest

    @defer.inlineCallbacks
    def test_influxdb_not_installed(self):
        captures = [capture.CaptureProperty('test_builder', 'test')]
        try:
            # Try to import
            import influxdb  # pylint: disable=import-outside-toplevel
            # consume it somehow to please pylint
            [influxdb]
        except ImportError:
            with self.assertRaises(config.ConfigErrors):
                InfluxStorageService("fake_url", 12345, "fake_user",
                                     "fake_password", "fake_db", captures)

        # if instead influxdb is installed, then initialize it - no errors
        # should be realized
        else:
            new_storage_backends = [
                InfluxStorageService("fake_url", 12345, "fake_user", "fake_password",
                                     "fake_db", captures)
            ]
            yield self.stats_service.reconfigService(new_storage_backends)

    @defer.inlineCallbacks
    def test_influx_storage_service_fake_install(self):
        # use a fake InfluxDBClient to test InfluxStorageService in systems which
        # don't have influxdb installed. Primarily useful for test coverage.
        self.patch(storage_backends.influxdb_client,
                   'InfluxDBClient', fakestats.FakeInfluxDBClient)
        captures = [capture.CaptureProperty('test_builder', 'test')]
        new_storage_backends = [InfluxStorageService(
            "fake_url", "fake_port", "fake_user", "fake_password", "fake_db", captures
        )]
        yield self.stats_service.reconfigService(new_storage_backends)

    def test_influx_storage_service_post_value(self):
        # test the thd_postStatsValue method of InfluxStorageService
        self.patch(storage_backends.influxdb_client,
                   'InfluxDBClient', fakestats.FakeInfluxDBClient)
        svc = InfluxStorageService(
            "fake_url", "fake_port", "fake_user", "fake_password", "fake_db", "fake_stats")
        post_data = {
            'name': 'test',
            'value': 'test'
        }
        context = {'x': 'y'}
        svc.thd_postStatsValue(post_data, "test_series_name", context)
        data = {
            'measurement': "test_series_name",
            'fields': {
                "name": "test",
                "value": "test"
            },
            'tags': {'x': 'y'}
        }
        points = [data]
        self.assertEqual(svc.client.points, points)

    def test_influx_service_not_inited(self):
        self.setUpLogging()
        self.patch(storage_backends.influxdb_client,
                   'InfluxDBClient', fakestats.FakeInfluxDBClient)
        svc = InfluxStorageService(
            "fake_url", "fake_port", "fake_user", "fake_password", "fake_db", "fake_stats")
        svc._inited = False
        svc.thd_postStatsValue("test", "test", "test")
        self.assertLogged("Service.*not initialized")


class TestStatsServicesConsumers(TestBuildStepMixin, TestStatsServicesBase):

    """
    Test the stats service from a fake step
    """

    @defer.inlineCallbacks
    def setUp(self):
        yield super().setUp()
        self.routingKey = (
            "builders", self.BUILDER_IDS[0], "builds", 1, "finished")
        self.master.mq.verifyMessages = False

    def setupBuild(self):
        self.master.db.insertTestData([
            fakedb.Build(id=1, masterid=1, workerid=1,
                         builderid=self.BUILDER_IDS[0],
                         buildrequestid=1, number=1),
        ])

    @defer.inlineCallbacks
    def setupFakeStorage(self, captures):
        self.fake_storage_service = fakestats.FakeStatsStorageService()
        self.fake_storage_service.captures = captures
        yield self.stats_service.reconfigService([self.fake_storage_service])

    def get_dict(self, build):
        return dict(
            buildid=1,
            number=build['number'],
            builderid=build['builderid'],
            buildrequestid=build['buildrequestid'],
            workerid=build['workerid'],
            masterid=build['masterid'],
            started_at=build['started_at'],
            complete=True,
            complete_at=build['complete_at'],
            state_string='',
            results=0,
        )

    @defer.inlineCallbacks
    def end_build_call_consumers(self):
        self.master.db.builds.finishBuild(buildid=1, results=0)
        build = yield self.master.db.builds.getBuild(buildid=1)
        self.master.mq.callConsumer(self.routingKey, self.get_dict(build))

    @defer.inlineCallbacks
    def test_property_capturing(self):
        self.setupFakeStorage(
            [capture.CaptureProperty('builder1', 'test_name')])
        self.setupBuild()
        self.master.db.builds.setBuildProperty(
            1, 'test_name', 'test_value', 'test_source')
        yield self.end_build_call_consumers()

        self.assertEqual([(
            {'name': 'test_name', 'value': 'test_value'},
            'builder1-test_name',
            {'build_number': '1', 'builder_name': 'builder1'}
        )], self.fake_storage_service.stored_data)

    @defer.inlineCallbacks
    def test_property_capturing_all_builders(self):
        self.setupFakeStorage(
            [capture.CapturePropertyAllBuilders('test_name')])
        self.setupBuild()
        self.master.db.builds.setBuildProperty(
            1, 'test_name', 'test_value', 'test_source')
        yield self.end_build_call_consumers()

        self.assertEqual([(
            {'name': 'test_name', 'value': 'test_value'},
            'builder1-test_name',
            {'build_number': '1', 'builder_name': 'builder1'}
        )], self.fake_storage_service.stored_data)

    @defer.inlineCallbacks
    def test_property_capturing_regex(self):
        self.setupFakeStorage(
            [capture.CaptureProperty('builder1', 'test_n.*', regex=True)])
        self.setupBuild()
        self.master.db.builds.setBuildProperty(
            1, 'test_name', 'test_value', 'test_source')
        yield self.end_build_call_consumers()

        self.assertEqual([(
            {'name': 'test_name', 'value': 'test_value'},
            'builder1-test_name',
            {'build_number': '1', 'builder_name': 'builder1'}
        )], self.fake_storage_service.stored_data)

    @defer.inlineCallbacks
    def test_property_capturing_error(self):
        self.setupFakeStorage([capture.CaptureProperty('builder1', 'test')])
        self.setupBuild()
        self.master.db.builds.setBuildProperty(
            1, 'test_name', 'test_value', 'test_source')
        self.master.db.builds.finishBuild(buildid=1, results=0)
        build = yield self.master.db.builds.getBuild(buildid=1)
        cap = self.fake_storage_service.captures[0]
        yield self.assertFailure(cap.consume(self.routingKey,
                                             self.get_dict(build)),
                                 CaptureCallbackError)

    @defer.inlineCallbacks
    def test_property_capturing_alt_callback(self):
        def cb(*args, **kwargs):
            return 'test_value'
        self.setupFakeStorage(
            [capture.CaptureProperty('builder1', 'test_name', cb)])
        self.setupBuild()
        self.master.db.builds.setBuildProperty(
            1, 'test_name', 'test_value', 'test_source')
        yield self.end_build_call_consumers()

        self.assertEqual([(
            {'name': 'test_name', 'value': 'test_value'},
            'builder1-test_name',
            {'build_number': '1', 'builder_name': 'builder1'}
        )], self.fake_storage_service.stored_data)

    @defer.inlineCallbacks
    def test_build_start_time_capturing(self):
        self.setupFakeStorage([capture.CaptureBuildStartTime('builder1')])
        self.setupBuild()
        yield self.end_build_call_consumers()

        self.assertEqual(
            'start-time', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_start_time_capturing_all_builders(self):
        self.setupFakeStorage([capture.CaptureBuildStartTimeAllBuilders()])
        self.setupBuild()
        yield self.end_build_call_consumers()

        self.assertEqual(
            'start-time', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_start_time_capturing_alt_callback(self):
        def cb(*args, **kwargs):
            return '2015-07-08T01:45:17.391018'
        self.setupFakeStorage([capture.CaptureBuildStartTime('builder1', cb)])
        self.setupBuild()
        yield self.end_build_call_consumers()

        self.assertEqual(
            'start-time', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_end_time_capturing(self):
        self.setupFakeStorage([capture.CaptureBuildEndTime('builder1')])
        self.setupBuild()
        yield self.end_build_call_consumers()

        self.assertEqual(
            'end-time', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_end_time_capturing_all_builders(self):
        self.setupFakeStorage([capture.CaptureBuildEndTimeAllBuilders()])
        self.setupBuild()
        yield self.end_build_call_consumers()

        self.assertEqual(
            'end-time', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_end_time_capturing_alt_callback(self):
        def cb(*args, **kwargs):
            return '2015-07-08T01:45:17.391018'
        self.setupFakeStorage([capture.CaptureBuildEndTime('builder1', cb)])
        self.setupBuild()
        yield self.end_build_call_consumers()

        self.assertEqual(
            'end-time', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def build_time_capture_helper(self, time_type, cb=None):
        self.setupFakeStorage([capture.CaptureBuildDuration('builder1', report_in=time_type,
                                                            callback=cb)])
        self.setupBuild()
        yield self.end_build_call_consumers()

    @defer.inlineCallbacks
    def test_build_duration_capturing_seconds(self):
        yield self.build_time_capture_helper('seconds')
        self.assertEqual(
            'duration', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_duration_capturing_minutes(self):
        yield self.build_time_capture_helper('minutes')
        self.assertEqual(
            'duration', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_duration_capturing_hours(self):
        yield self.build_time_capture_helper('hours')
        self.assertEqual(
            'duration', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    def test_build_duration_report_in_error(self):
        with self.assertRaises(config.ConfigErrors):
            capture.CaptureBuildDuration('builder1', report_in='foobar')

    @defer.inlineCallbacks
    def test_build_duration_capturing_alt_callback(self):
        def cb(*args, **kwargs):
            return 10
        yield self.build_time_capture_helper('seconds', cb)
        self.assertEqual(
            'duration', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_duration_capturing_all_builders(self):
        self.setupFakeStorage([capture.CaptureBuildDurationAllBuilders()])
        self.setupBuild()
        yield self.end_build_call_consumers()

        self.assertEqual(
            'duration', list(self.fake_storage_service.stored_data[0][0].keys())[0])

    @defer.inlineCallbacks
    def test_build_times_capturing_error(self):
        def cb(*args, **kwargs):
            raise TypeError
        self.setupFakeStorage([capture.CaptureBuildStartTime('builder1', cb)])
        self.setupBuild()
        self.master.db.builds.setBuildProperty(
            1, 'test_name', 'test_value', 'test_source')
        self.master.db.builds.finishBuild(buildid=1, results=0)
        build = yield self.master.db.builds.getBuild(buildid=1)
        cap = self.fake_storage_service.captures[0]
        yield self.assertFailure(cap.consume(self.routingKey,
                                             self.get_dict(build)),
                                 CaptureCallbackError)

        self.setupFakeStorage([capture.CaptureBuildEndTime('builder1', cb)])
        cap = self.fake_storage_service.captures[0]
        yield self.assertFailure(cap.consume(self.routingKey,
                                             self.get_dict(build)),
                                 CaptureCallbackError)

        self.setupFakeStorage(
            [capture.CaptureBuildDuration('builder1', callback=cb)])
        cap = self.fake_storage_service.captures[0]
        yield self.assertFailure(cap.consume(self.routingKey,
                                             self.get_dict(build)),
                                 CaptureCallbackError)

    @defer.inlineCallbacks
    def test_yield_metrics_value(self):
        self.setupFakeStorage([capture.CaptureBuildStartTime('builder1')])
        self.setupBuild()
        yield self.end_build_call_consumers()
        yield self.stats_service.yieldMetricsValue('test', {'test': 'test'}, 1)

        build_data = yield self.stats_service.master.data.get(('builds', 1))
        routingKey = ("stats-yieldMetricsValue", "stats-yield-data")

        msg = {
            'data_name': 'test',
            'post_data': {'test': 'test'},
            'build_data': build_data
        }

        exp = [(routingKey, msg)]
        self.stats_service.master.mq.assertProductions(exp)

    @defer.inlineCallbacks
    def test_capture_data(self):
        self.setupFakeStorage([capture.CaptureData('test', 'builder1')])
        self.setupBuild()
        self.master.db.builds.finishBuild(buildid=1, results=0)
        build_data = yield self.stats_service.master.data.get(('builds', 1))

        msg = {
            'data_name': 'test',
            'post_data': {'test': 'test'},
            'build_data': build_data
        }

        routingKey = ("stats-yieldMetricsValue", "stats-yield-data")
        self.master.mq.callConsumer(routingKey, msg)
        self.assertEqual([(
            {'test': 'test'},
            'builder1-test',
            {'build_number': '1', 'builder_name': 'builder1'}
        )], self.fake_storage_service.stored_data)

    @defer.inlineCallbacks
    def test_capture_data_all_builders(self):
        self.setupFakeStorage([capture.CaptureDataAllBuilders('test')])
        self.setupBuild()
        self.master.db.builds.finishBuild(buildid=1, results=0)
        build_data = yield self.stats_service.master.data.get(('builds', 1))

        msg = {
            'data_name': 'test',
            'post_data': {'test': 'test'},
            'build_data': build_data
        }

        routingKey = ("stats-yieldMetricsValue", "stats-yield-data")
        self.master.mq.callConsumer(routingKey, msg)
        self.assertEqual([(
            {'test': 'test'},
            'builder1-test',
            {'build_number': '1', 'builder_name': 'builder1'}
        )], self.fake_storage_service.stored_data)

    @defer.inlineCallbacks
    def test_capture_data_alt_callback(self):
        def cb(*args, **kwargs):
            return {'test': 'test'}
        self.setupFakeStorage([capture.CaptureData('test', 'builder1', cb)])
        self.setupBuild()
        self.master.db.builds.finishBuild(buildid=1, results=0)
        build_data = yield self.stats_service.master.data.get(('builds', 1))

        msg = {
            'data_name': 'test',
            'post_data': {'test': 'test'},
            'build_data': build_data
        }

        routingKey = ("stats-yieldMetricsValue", "stats-yield-data")
        self.master.mq.callConsumer(routingKey, msg)
        self.assertEqual([(
            {'test': 'test'},
            'builder1-test',
            {'build_number': '1', 'builder_name': 'builder1'}
        )], self.fake_storage_service.stored_data)

    @defer.inlineCallbacks
    def test_capture_data_error(self):
        def cb(*args, **kwargs):
            raise TypeError
        self.setupFakeStorage([capture.CaptureData('test', 'builder1', cb)])
        self.setupBuild()
        self.master.db.builds.finishBuild(buildid=1, results=0)
        build_data = yield self.stats_service.master.data.get(('builds', 1))

        msg = {
            'data_name': 'test',
            'post_data': {'test': 'test'},
            'build_data': build_data
        }

        routingKey = ("stats-yieldMetricsValue", "stats-yield-data")
        cap = self.fake_storage_service.captures[0]
        yield self.assertFailure(cap.consume(routingKey, msg),
                                 CaptureCallbackError)
