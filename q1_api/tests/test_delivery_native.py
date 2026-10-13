"""Real export delivery: selection, immutable bytes, deduplication and API restart."""
from delivery_experiment import run_scenario


def test_native_full_sample_export_delivers_only_new_stage2_approved_cases(tmp_path):
    # Wrong selection, changed CSV bytes, or reused keys must fail this HTTP lifecycle.
    result = run_scenario(tmp_path, 'full-flow')
    assert result['pass'], result.get('error', result)
    assert [item['row_count'] for item in result['exports']] == [2, 1]
    assert len(result['deliveries']) == 2


def test_native_lost_receipt_retries_one_ingestion_with_same_bytes_and_key(tmp_path):
    # A new key after a timeout would produce duplicate upstream ingestion.
    result = run_scenario(tmp_path, 'lost-response')
    assert result['pass'], result.get('error', result)
    assert len(result['deliveries']) == 1
    assert result['deliveries'][0]['attempts'] >= 2


def test_native_sigkill_after_ingestion_recovers_automatically_without_duplicate(tmp_path):
    # Losing a claimed export or regenerating its key/bytes on restart must fail.
    result = run_scenario(tmp_path, 'sigkill')
    assert result['pass'], result.get('error', result)
    assert result['crash']['returncode'] == -9
    assert len(result['deliveries']) == 1
