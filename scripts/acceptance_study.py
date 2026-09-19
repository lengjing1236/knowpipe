"""Check the offline labeling UI; synthetic test grades are never study results."""
import argparse
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    errors=[]
    with sync_playwright() as p:
        settings_file=Path('/tmp/knowpipe-chromium.json')
        settings=json.loads(settings_file.read_text()) if settings_file.exists() else {}
        browser=p.chromium.launch(headless=True,executable_path=os.environ.get('BROWSER_EXECUTABLE') or settings.get('executable'),args=settings.get('args'))
        page=browser.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto((args.study_dir/'label.html').resolve().as_uri())
        selects=page.locator('select');count=selects.count()
        assert count>0
        assert page.locator('select').evaluate_all("nodes=>nodes.every(n=>n.value==='')")
        page.locator('#save').click()
        assert '全部标注' in page.locator('#status').inner_text()
        page.locator('#reviewer').fill('automated-ui-test-not-human')
        for select in selects.all(): select.select_option('0')
        with page.expect_download() as download:
            page.locator('#save').click()
        labels=json.loads(Path(download.value.path()).read_text())
        assert sum(len(q['judgments']) for q in labels['queries'])==count
        assert labels['reviewer']=='automated-ui-test-not-human'
        assert not errors,errors
        download.value.delete()
        browser.close()
    report={'candidate_count':count,'checks':['initial labels blank','incomplete labels rejected','downloaded JSON schema'],
            'javascript_errors':errors,'note':'synthetic UI smoke only; test download deleted; no human evaluation performed'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':main()
