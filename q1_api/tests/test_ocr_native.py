"""Real API + unchanged OCR processes: complete HTTP flow and a SIGKILL boundary."""
import csv
import io

from ocr_experiment import SAMPLE, cases, comparison, process_round, recover, service, upload


def test_native_sample_ocr_review_and_reprocessing_preserves_approved_case(tmp_path):
    # Catches dropped vendor fields, wrong failure classification, and review overwrite.
    with service('mock', tmp_path, config={'OCR_FAIL_RATE': '0', 'OCR_HANG_RATE': '0'}) as (mock, _):
        with service('api', tmp_path, database=tmp_path / 'db.sqlite3',
                     ocr_url=str(mock.base_url).rstrip('/') + '/ocr/parse') as (api, _):
            batch = upload(api)
            assert (batch['accepted_count'], batch['rejected_count']) == (43, 7)
            first = process_round(api, batch['id'])
            items = first['cases']
            assert first['batch']['status_counts']['ocr_done'] == 38
            assert first['batch']['status_counts']['ocr_failed'] == 5
            source = dict(enumerate(csv.DictReader(io.StringIO(SAMPLE.read_text())), start=2))
            for item in items:
                original = source[item['row_number']]
                assert {key: item[key] for key in original} == original
                assert item['ocr_attempts'] == 1
                assert not item['ocr_retryable']
                if 'badscan' in item['image_ref'].lower():
                    assert item['ocr_fields'] is None
                    assert item['ocr_error'] == (
                        f"cannot read identity-card fields from '{item['image_ref']}' (scan quality)")
                    assert item['ocr_error_code']
                else:
                    assert set(item['ocr_fields']) == {'id_number', 'full_name', 'dob', 'issue_date', 'confidence'}
                    assert item['ocr_error'] is None
            case = next(item for item in items if item['status'] == 'ocr_done')
            known = next(item for item in items if item['case_ref'] == 'APPL-2026-0001')
            assert known['ocr_fields'] == {
                'id_number': 'TEST-ID-830454', 'full_name': 'Test Applicant 0454',
                'dob': '1969-11-19', 'issue_date': '2020-06-06', 'confidence': 0.94}
            url = f"/api/cases/{case['id']}/review"
            assert api.post(url, json={'stage': 2, 'decision': 'approve'}).status_code == 409
            assert api.post(url, json={'stage': 1, 'decision': 'approve'}).json()['status'] == 'stage1_approved'
            assert api.post(url, json={'stage': 2, 'decision': 'approve'}).json()['status'] == 'stage2_approved'
            approved = next(item for item in cases(api, batch['id']) if item['id'] == case['id'])
            before = mock.get('/debug/requests').json()
            again = process_round(api, batch['id'])
            assert next(item for item in again['cases'] if item['id'] == case['id']) == approved
            assert mock.get('/debug/requests').json() == before
            assert len(before) == 43


def test_native_sigkill_late_result_recovers_to_clean_fields_and_status(tmp_path):
    # Catches loss of interrupted work and stale recovery state; no production test hooks.
    rows = list(csv.DictReader(io.StringIO(SAMPLE.read_text())))
    selected = rows[:4] + [next(row for row in rows if 'badscan' in row['image_ref'].lower())]
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(selected)
    content = stream.getvalue().encode()
    config = {'OCR_FAIL_RATE': '0', 'OCR_HANG_RATE': '1', 'OCR_HANG_SECONDS': '0.3'}
    clean = recover(tmp_path / 'clean', 42, interrupted=False, content=content, mock_config=config)
    crashed = recover(tmp_path / 'crashed', 42, interrupted=True, content=content,
                      mock_config=config, threshold=2)
    assert clean['pass'], clean
    assert crashed['pass'], crashed
    assert crashed['crash']['returncode'] == -9
    assert crashed['after_restart']['ocr_run']['status'] == 'interrupted'
    assert len(crashed['rounds']) <= 3
    assert comparison(clean['cases']) == comparison(crashed['cases'])
    late_image = crashed['crash']['requests'][-1]['image_ref']
    assert sum(record['image_ref'] == late_image for record in crashed['requests']) >= 2
