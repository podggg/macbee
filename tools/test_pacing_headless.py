"""Explicit review lanes. No GUI, app-bundle launch, device probe or real receiver."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'Tests'))
PYTHON_MODULES = """
application_protocol application_timing backend_counters browser_fixture configured_transport
coreaudio_protocol diagnostics local_config parent_watch portable_runtime receiver_registry
receiver_setup relay_runtime sender_recovery_unit server_clock session_diagnostics speaker_transport
system_audio_protocol system_pacing server_degraded ui_contract
""".split()
PYTHON = ['test_'+name for name in PYTHON_MODULES] + """
test_protocol.PeerProtocolTests
test_application_pcm.ApplicationPCMTests.test_current_builds_do_not_link_ordinary_audio
test_application_source.ApplicationSourceTests.test_identity_resolver_has_no_browser_allowlist
test_application_source.ApplicationSourceTests.test_selected_capture_never_terminates_a_user_app
test_system_audio_source.SystemAudioSourceTests.test_only_explicit_future_build_enables_system_mode
test_controlled_source.ControlledSourceTests.test_adapter_has_no_global_or_microphone_mode
test_controlled_source.ControlledSourceTests.test_capture_bench_does_not_start_on_launch
test_reader_toolchain.ReaderToolchainTests.test_compiler_and_selected_target_sdk_are_recorded_without_host_paths
""".split()
PYTHON += ['test_capture_protocol.CaptureProtocolTests.test_'+name for name in
           'fixture_fragmentation_and_content bad_headers_and_samples truncated_and_zero_only_not_success '
           'after_end_and_length_bound consumer_eof_timeout_and_pipe_failure counter_evidence_is_nonzero_not_padding'.split()]
NATIVE = """
test_application_session test_capture_diagnostics test_pcm_ring test_system_session test_recovery_lifecycle
test_application_pcm.ApplicationPCMTests.test_offline_normal_amplitude_converter
test_capture_protocol.CaptureProtocolTests.test_native_converter_cross_language
test_controlled_source.ControlledSourceTests.test_generator_without_opening_audio
test_application_source.ApplicationSourceTests.test_metadata_fixtures_without_live_lookup
test_application_source.ApplicationSourceTests.test_browser_fixtures_without_live_lookup
test_application_source.ApplicationSourceTests.test_dia_uses_same_identity_resolver_without_live_lookup
test_application_source.ApplicationSourceTests.test_unlisted_app_uses_same_resolver_without_brand_table
test_system_audio_source.SystemAudioSourceTests.test_both_source_modes_with_unrelated_and_renderer_markers
test_system_audio_source.SystemAudioSourceTests.test_owned_renderer_exclusion_without_live_lookup_or_capture
test_reader_toolchain.ReaderToolchainTests.test_actual_release_configuration_selects_the_compiler_sdk
""".split()
STAGE = ['test_pacing_process', 'test_server_clock_process', 'test_snapcast_qos']

if __name__ == '__main__':
    lanes = dict(python=PYTHON,native=NATIVE,stage=STAGE)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('lane',choices=lanes)
    lane=parser.parse_args().lane
    with tempfile.TemporaryDirectory(prefix='macbee-headless-runtime-') as home:
        os.environ['MACBEE_RUNTIME_HOME']=home
        os.environ.pop('MACBEE_ASSET_ROOT',None);os.environ.pop('MACBEE_PROBE',None)
        suite=unittest.defaultTestLoader.loadTestsFromNames(lanes[lane])
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        sys.exit(not result.wasSuccessful())
