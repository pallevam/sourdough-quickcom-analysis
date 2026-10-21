"""Exercise the generic dashboard against the local built warehouse."""
import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard'))
DB = Path(os.environ.get('QCOM_DB', ROOT / 'data/qcom-observations-v2.duckdb'))
pytestmark = pytest.mark.skipif(not DB.exists(), reason='Build the warehouse for UI integration tests')


def test_explorer_views_and_filters():
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['authed'] = True
    app.run()
    assert not app.exception
    assert not app.radio
    assert int(app.metric[0].value.replace(',', '')) > 28000
    app.multiselect(key='platforms_real').set_value(['zepto']).run()
    assert not app.exception and app.metric[1].value == '1'
    app.text_input(key='search_real').set_value('unlikelymissingproduct123').run()
    assert not app.exception and any('No listings' in x.value for x in app.info)
    app.text_input(key='search_real').set_value('').run()
    app.multiselect(key='platforms_real').set_value([]).run()
    for view in app.selectbox(key='view').options:
        if view == 'Ask':  # rendering setup only; never send an API request
            continue
        app.selectbox(key='view').select(view).run()
        assert not app.exception, view
    app.selectbox(key='view').select('Assortment').run()


def test_ask_exposes_data_and_direct_reports_without_api(monkeypatch):
    import anthropic
    def no_api(*args, **kwargs):
        raise AssertionError('Direct browsing must not call the model')
    monkeypatch.setattr(anthropic.resources.messages.Messages, 'create', no_api)
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['authed'] = True
    app.run()
    app.selectbox(key='view').select('Ask').run()
    assert not app.exception
    assert any('What data can I query?' in h.value for h in app.subheader)
    app.selectbox(key='browse_report').select('products').run()
    app.selectbox(key='browse_platform').select('bigbasket').run()
    app.text_input(key='browse_search').set_value('bread').run()
    app.button(key='browse_run').click().run()
    assert not app.exception
    results = [df.value for df in app.dataframe if 'product' in df.value.columns]
    assert len(results) == 1 and not results[0].empty
    assert results[0]['product'].str.contains('bread', case=False).all()
