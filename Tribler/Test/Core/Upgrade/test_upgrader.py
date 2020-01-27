from __future__ import absolute_import

import os
import shutil
from pathlib import Path

from pony.orm import db_session

from twisted.internet.defer import Deferred, inlineCallbacks

from Tribler.Core import version
from Tribler.Core.Modules.MetadataStore.OrmBindings.channel_metadata import CHANNEL_DIR_NAME_LENGTH
from Tribler.Core.Modules.MetadataStore.store import MetadataStore
from Tribler.Core.Upgrade.upgrade import TriblerUpgrader, cleanup_noncompliant_channel_torrents
from Tribler.Core.Utilities.configparser import CallbackConfigParser
from Tribler.Core.simpledefs import NTFY_STARTED, NTFY_UPGRADER_TICK, STATEDIR_CHANNELS_DIR, STATEDIR_CHECKPOINT_DIR, \
    STATEDIR_DB_DIR, STATEDIR_WALLET_DIR
from Tribler.Test.Core.Upgrade.upgrade_base import AbstractUpgrader
from Tribler.Test.tools import trial_timeout


class TestUpgrader(AbstractUpgrader):

    @inlineCallbacks
    def setUp(self):
        yield super(TestUpgrader, self).setUp()
        self.upgrader = TriblerUpgrader(self.session)

    @trial_timeout(10)
    def test_update_status_text(self):
        test_deferred = Deferred()

        def on_upgrade_tick(subject, changetype, objectID, status_text):
            self.assertEqual(status_text, "12345")
            test_deferred.callback(None)

        self.session.notifier.add_observer(on_upgrade_tick, NTFY_UPGRADER_TICK, [NTFY_STARTED])
        self.upgrader.update_status("12345")
        return test_deferred

    @trial_timeout(10)
    @inlineCallbacks
    def test_upgrade_72_to_pony(self):
        OLD_DB_SAMPLE = os.path.abspath(os.path.join(os.path.abspath(
            os.path.dirname(os.path.realpath(__file__))), '..', 'data', 'upgrade_databases', 'tribler_v29.sdb'))
        old_database_path = os.path.join(self.session.config.get_state_dir(), 'sqlite', 'tribler.sdb')
        new_database_path = os.path.join(self.session.config.get_state_dir(), 'sqlite', 'metadata.db')
        shutil.copyfile(OLD_DB_SAMPLE, old_database_path)

        yield self.upgrader.run()
        channels_dir = os.path.join(self.session.config.get_chant_channels_dir())
        mds = MetadataStore(new_database_path, channels_dir, self.session.trustchain_keypair)
        with db_session:
            self.assertEqual(mds.TorrentMetadata.select().count(), 24)
        mds.shutdown()

    @trial_timeout(10)
    @inlineCallbacks
    def test_upgrade_pony_db_6to7(self):
        """
        Test that channels and torrents with forbidden words are cleaned up during upgrade from Pony db ver 6 to 7.
        Also, check that the DB version is upgraded.
        :return:
        """
        OLD_DB_SAMPLE = os.path.abspath(os.path.join(os.path.abspath(
            os.path.dirname(os.path.realpath(__file__))), '..', 'data', 'upgrade_databases', 'pony_v6.db'))
        old_database_path = os.path.join(self.session.config.get_state_dir(), 'sqlite', 'metadata.db')
        shutil.copyfile(OLD_DB_SAMPLE, old_database_path)

        yield self.upgrader.run()
        channels_dir = os.path.join(self.session.config.get_chant_channels_dir())
        mds = MetadataStore(old_database_path, channels_dir, self.session.trustchain_keypair)
        with db_session:
            self.assertEqual(mds.TorrentMetadata.select().count(), 23)
            self.assertEqual(mds.ChannelMetadata.select().count(), 2)
            self.assertEqual(int(mds.MiscData.get(name="db_version").value), 7)
        mds.shutdown()

    @trial_timeout(10)
    @inlineCallbacks
    def test_skip_upgrade_72_to_pony(self):
        OLD_DB_SAMPLE = os.path.abspath(os.path.join(os.path.abspath(
            os.path.dirname(os.path.realpath(__file__))), '..', 'data', 'upgrade_databases', 'tribler_v29.sdb'))
        old_database_path = os.path.join(self.session.config.get_state_dir(), 'sqlite', 'tribler.sdb')
        new_database_path = os.path.join(self.session.config.get_state_dir(), 'sqlite', 'metadata.db')
        channels_dir = os.path.join(self.session.config.get_chant_channels_dir())

        shutil.copyfile(OLD_DB_SAMPLE, old_database_path)

        self.upgrader.skip()
        yield self.upgrader.run()
        mds = MetadataStore(new_database_path, channels_dir, self.session.trustchain_keypair)
        with db_session:
            self.assertEqual(mds.TorrentMetadata.select().count(), 0)
            self.assertEqual(mds.ChannelMetadata.select().count(), 0)
        mds.shutdown()

    def test_delete_noncompliant_state(self):
        STATE_DIR = os.path.abspath(os.path.join(os.path.abspath(
            os.path.dirname(os.path.realpath(__file__))), '..', 'data', 'noncompliant_state_dir'))
        tmpdir = os.path.join(self.temporary_directory(), os.path.basename(STATE_DIR))
        shutil.copytree(STATE_DIR, tmpdir)
        cleanup_noncompliant_channel_torrents(tmpdir)

        # Check cleanup of the channels dir
        dir_listing = os.listdir(os.path.join(tmpdir, "channels"))
        self.assertEqual(3, len(dir_listing))
        for f in os.listdir(os.path.join(tmpdir, "channels")):
            self.assertEqual(CHANNEL_DIR_NAME_LENGTH, len(os.path.splitext(f)[0]))

        # Check cleanup of torrent state dir
        checkpoints_dir = os.path.join(tmpdir, "dlcheckpoints")
        dir_listing = os.listdir(checkpoints_dir)
        self.assertEqual(1, len(dir_listing))
        file_path = os.path.join(checkpoints_dir, dir_listing[0])
        pstate = CallbackConfigParser()
        pstate.read_file(file_path)
        self.assertEqual(CHANNEL_DIR_NAME_LENGTH, len(pstate.get('state', 'metainfo')['info']['name']))


class TestUpgraderStateDirectory(AbstractUpgrader):

    @inlineCallbacks
    def setUp(self):
        yield super(TestUpgraderStateDirectory, self).setUp()
        self.upgrader = TriblerUpgrader(self.session)
        self.original_version = version.version_id

    @inlineCallbacks
    def tearDown(self):
        version.version_id = self.original_version
        yield super(TestUpgraderStateDirectory, self).tearDown()

    def test_state_directory_upgrade(self):
        """
        Test if a new state directory is created with proper files and directories on upgrade.
        """
        # Note that, the default version set in the code is 7.0.0-GIT.
        # So for the first time after running the (enabled) upgrader,
        # a new state directory for this version should be created.
        self.upgrader.run()

        version_state_dir = self.session.config.get_state_dir(version_id=version.version_id)
        self.assertTrue(os.path.exists(version_state_dir))
        version_state_sub_dirs = os.listdir(version_state_dir)
        backup_dirs = [STATEDIR_DB_DIR, STATEDIR_CHECKPOINT_DIR, STATEDIR_WALLET_DIR, STATEDIR_CHANNELS_DIR]
        for backup_dir in backup_dirs:
            self.assertTrue(backup_dir in version_state_sub_dirs)

        # Creating keypairs to test that these key pairs are also copied
        self.upgrader.session.init_keypair()

        # Now lets increase the version and test that upgrader does backup the directory and copy proper files
        version.version_id = '7.5.0'

        # If the version backup is not enabled, upgrader will not try to create a backup of the previous state directory
        # and create a new state directory for the new version.
        self.session.config.set_version_backup_enabled(False)
        self.upgrader.run()

        version_state_dir = Path(self.session.config.get_state_dir(version_id=version.version_id))
        self.assertFalse(os.path.exists(version_state_dir))

        self.session.config.set_version_backup_enabled(True)
        self.upgrader.run()

        version_state_dir = Path(self.session.config.get_state_dir(version_id=version.version_id))
        self.assertTrue(os.path.exists(version_state_dir))
        version_state_sub_dirs = os.listdir(version_state_dir)
        backup_dirs = [STATEDIR_DB_DIR, STATEDIR_CHECKPOINT_DIR, STATEDIR_WALLET_DIR, STATEDIR_CHANNELS_DIR]
        for backup_dir in backup_dirs:
            self.assertTrue(backup_dir in version_state_sub_dirs)

        self.assertTrue(len(list(version_state_dir.glob("*.pem"))) > 1)
