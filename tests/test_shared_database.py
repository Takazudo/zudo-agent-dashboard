"""Shared database serving contract; disposable state only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from zudo_agent import cli, server
from zudo_agent.store import Store


class SharedDatabaseTests(unittest.TestCase):
    def test_no_collect_serves_and_closes_without_worker(self):
        http = Mock(server_port=1234)
        with patch.object(server, 'make_server', return_value=http), patch.object(server.threading, 'Thread') as thread:
            server.serve({'machine': 'fixture'}, 'fixture.sqlite', 1234, collect=False)
        thread.assert_not_called()
        http.serve_forever.assert_called_once()
        http.server_close.assert_called_once()

    def test_default_still_collects(self):
        http = Mock(server_port=1234)
        with patch.object(server, 'make_server', return_value=http), patch.object(server.threading, 'Thread') as thread:
            server.serve({'machine': 'fixture'}, 'fixture.sqlite', 1234)
        thread.return_value.start.assert_called_once()
        thread.return_value.join.assert_called_once_with(timeout=5)

    def test_cli_no_collect_keeps_explicit_database(self):
        config = {'machine': 'fixture'}
        with patch('sys.argv', ['zudo-agent', '--config', 'fixture.json', '--db', 'shared.sqlite', 'serve', '--no-collect']), patch.object(cli, 'load_config', return_value=config), patch.object(cli, 'serve') as serve:
            cli.main()
        serve.assert_called_once_with(config, 'shared.sqlite', 8765, console_policy=None, collect=False)

    def test_shared_hook_write_and_discovery_remain_distinct(self):
        config = dict(machine='fixture', stale_after=120, projects={'project': {'repository': 'fixture/project'}})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'shared.sqlite'
            hook, dashboard = Store(path, config), Store(path, config)
            try:
                event = dict(project_id='project', run_id='a' * 64, machine='fixture', source='tmux', kind='discovered', observed_at=100)
                dashboard.discovery([event], True, 1, 0, 100)
                run = dashboard.snapshot(100)['projects'][0]['runs'][0]
                self.assertEqual((run['state'], run['evidence']), ('unknown', 'metadata-only'))
                hook.ingest(dict(event, source='codex', kind='permission-request', observed_at=101))
                dashboard.discovery([dict(event, observed_at=300)], True, 1, 0, 300)
                run = dashboard.snapshot(300)['projects'][0]['runs'][0]
                self.assertEqual((run['state'], run['evidence']), ('needs-attention', 'lifecycle'))
                self.assertEqual((run['freshness'], run['state_freshness']), ('fresh', 'stale'))
                self.assertNotIn('confidence', run)
            finally:
                hook.close()
                dashboard.close()
