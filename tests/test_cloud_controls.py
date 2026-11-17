import json
import unittest
from unittest.mock import Mock, patch

from orca_flashforge.cloud_controls import dispatch, cancel_print
from orca_flashforge.model import FlashForgeError


class CloudControlTests(unittest.TestCase):
    def test_confirmed_broker_delivery_is_not_device_confirmation(self):
        session = Mock(client_id='client')
        session._devices = {'device': {'devTopic': 'received-topic'}}
        session.mqtt_config.return_value = {'server': 'synthetic'}
        with patch('orca_flashforge.cloud_controls.publish') as publish:
            for kind, value, expected in (
                ('light', 'open', {'cmd': 'lightControl_cmd', 'args': {'status': 'open'}}),
                ('light', 'close', {'cmd': 'lightControl_cmd', 'args': {'status': 'close'}}),
                ('camera', 'open', {'cmd': 'streamCtrl_cmd', 'args': {'action': 'open'}}),
            ):
                result = dispatch(session, 'device', kind, value)
                self.assertTrue(result['broker_confirmed'])
                self.assertFalse(result['device_confirmed'])
                self.assertEqual(publish.call_args.args[2], 'received-topic')
                self.assertEqual(json.loads(publish.call_args.args[3]), expected)

    def test_unknown_action_and_missing_device_never_publish(self):
        session = Mock(client_id='client')
        session._devices = {}
        with patch('orca_flashforge.cloud_controls.publish') as publish:
            for args in [('unknown', 'open'), ('light', 'invalid'), ('light', 'open')]:
                with self.assertRaises(FlashForgeError):
                    dispatch(session, 'other', *args)
            publish.assert_not_called()

    def test_unknown_delivery_reports_uncertainty(self):
        session = Mock(client_id='client')
        session._devices = {'device': {'devTopic': 'received-topic'}}
        with patch('orca_flashforge.cloud_controls.publish', side_effect=OSError):
            with self.assertRaisesRegex(FlashForgeError, 'no confirmó'):
                dispatch(session, 'device', 'camera', 'open')

    def test_cancel_requires_the_current_job_and_does_not_claim_device_confirmation(self):
        session = Mock(client_id='client')
        session._devices = {'device': {'devTopic': 'received-topic'}}
        session.status.return_value = {'job_id': 'job-1', 'status': 'printing'}
        with patch('orca_flashforge.cloud_controls.publish') as publish:
            with self.assertRaises(FlashForgeError):
                cancel_print(session, 'device', 'old-job')
            publish.assert_not_called()
            result = cancel_print(session, 'device', 'job-1')
            self.assertFalse(result['device_confirmed'])
            self.assertEqual(json.loads(publish.call_args.args[3]),
                             {'cmd': 'jobCtl_cmd', 'args': {'jobID': 'job-1', 'action': 'cancel'}})
            session.status.return_value['status'] = 'completed'
            with self.assertRaises(FlashForgeError):
                cancel_print(session, 'device', 'job-1')
