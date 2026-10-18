"""Exercise the generic dashboard against the local built warehouse."""
import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dashboard'))
DB = Path(os.environ.get('QCOM_DB', ROOT / 'data/qcom.duckdb'))
pytestmark = pytest.mark.skipif(not DB.exists(), reason='Build the warehouse for UI integration tests')


def test_explorer_views_filters_and_demo():
    app = AppTest.from_file(str(ROOT / 'dashboard/app.py'), default_timeout=30)
    app.session_state['authed'] = True
    app.run()
    assert not app.exception
    assert app.radio(key='dataset').value == 'Real observations'
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
    app.radio(key='dataset').set_value('Synthetic demo').run()
    assert not app.exception and app.warning
    assert int(app.metric[0].value.replace(',', '')) == 42
