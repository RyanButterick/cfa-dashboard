"""International Portfolio — placeholder page."""

import streamlit as st

st.set_page_config(
    page_title="International Portfolio",
    page_icon="\U0001f30d",
    layout="wide",
)

st.markdown(
    '<div style="text-align:center; padding:100px 20px;">'
    '<div style="font-size:4.5em; margin-bottom:20px;">\u2699\ufe0f</div>'
    '<h1 style="color:#0B2E33; margin-bottom:8px; font-weight:700;">International Portfolio</h1>'
    '<p style="color:#4F7C82; font-size:1.15em; max-width:540px; margin:0 auto; line-height:1.7;">'
    'Cross-border equity analysis with multi-currency support, '
    'ADR/GDR tracking, and region-specific macro event integration.'
    '</p>'
    '<div style="margin-top:32px; padding:14px 28px; background:#F0F7F8; '
    'border:1px solid #B8E3E9; border-radius:8px; display:inline-block;">'
    '<span style="color:#4F7C82; font-weight:600; font-size:0.95em;">'
    '\U0001f6a7 &nbsp; Under Development &mdash; Coming Soon</span>'
    '</div>'
    '</div>',
    unsafe_allow_html=True,
)
