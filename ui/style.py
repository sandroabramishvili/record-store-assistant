"""The retro look beyond what the theme (.streamlit/config.toml) can express.

Colours and fonts live in the theme. This adds the parts that make it a 1970s record shop:
the marquee header with a vinyl disc and sunset stripes, LP-sleeve record cards with a hard
offset shadow, mustard price stickers, "SOLD"-style status stamps, and the ticket-stub
confirmation box.

Only our OWN class names and keyed containers (`.st-key-<key>`, which Streamlit generates
from `key=`) are styled, never Streamlit's internal classes, so an upgrade can't break it.
"""

import html

import streamlit as st

INK, PAPER, CARD, ORANGE, MUSTARD, RUST, BROWN = '#2b1d14', '#f3e9d2', '#fbf4e2', '#c4501b', '#e3a72f', '#a8321e', '#7a4a2a'

CSS = f"""
<style>
/* ---- marquee header ---- */
.rs-marquee {{ display:flex; align-items:center; gap:1rem; margin:-0.5rem 0 0.25rem; }}
.rs-brand {{ font-family:'Shrikhand', serif; font-size:2rem; line-height:1; color:{INK};
             text-shadow:3px 3px 0 {MUSTARD}; }}
.rs-tagline {{ font-family:'Courier Prime', monospace; letter-spacing:.18em; text-transform:uppercase;
               font-size:.78rem; color:{BROWN}; margin-top:.35rem; }}
.rs-stripes {{ height:14px; margin:.4rem 0 1rem;
               background:linear-gradient({ORANGE} 0 25%, {MUSTARD} 25% 50%, {BROWN} 50% 75%, {INK} 75% 100%); }}
/* the record: spindle hole, orange label with a mustard ring, black vinyl with grooves, a glint */
.rs-disc {{ flex:none; border-radius:50%; box-shadow:3px 3px 0 {MUSTARD};
            background:
              linear-gradient(135deg, transparent 38%, #ffffff26 47%, transparent 56%),
              radial-gradient(circle, {PAPER} 0 4%, {ORANGE} 4.5% 24%, {MUSTARD} 24.5% 27%, {ORANGE} 27.5% 33%,
                              transparent 33.5% 100%),
              repeating-radial-gradient(circle, {INK} 0 2px, #4a3626 2px 3px); }}
.rs-disc-sm {{ width:58px; height:58px; }}
.rs-disc-lg {{ width:120px; height:120px; }}
.rs-disc.spin {{ animation:rs-spin 1.8s linear infinite; }}
@keyframes rs-spin {{ to {{ transform:rotate(360deg); }} }}
@media (prefers-reduced-motion: reduce) {{ .rs-disc.spin {{ animation:none; }} }}

/* ---- LP-sleeve record cards (containers keyed "card-...") ---- */
[class*="st-key-card-"] {{ background:{CARD}; border:2px solid {INK}; border-radius:4px;
                           box-shadow:5px 5px 0 {INK}; padding:.75rem 1rem; margin:.35rem .4rem .9rem 0; }}
.rs-card-title {{ font-weight:700; font-size:1.05rem; color:{INK}; }}
.rs-card-meta {{ font-family:'Courier Prime', monospace; font-size:.85rem; color:{BROWN}; }}
.rs-track {{ display:inline-block; min-width:1.7rem; padding:.05rem .35rem; margin-right:.5rem; text-align:center;
             background:{INK}; color:{PAPER}; font-family:'Courier Prime', monospace; font-weight:700;
             border-radius:50%; }}
.rs-price {{ display:inline-block; padding:.15rem .55rem; background:{MUSTARD}; color:{INK};
             border:1.5px dashed {INK}; font-family:'Courier Prime', monospace; font-weight:700;
             font-size:1.05rem; transform:rotate(-4deg); }}
.rs-stamp {{ display:inline-block; padding:0 .4rem; border:2px solid {RUST}; color:{RUST};
             font-family:'Courier Prime', monospace; font-weight:700; letter-spacing:.12em;
             text-transform:uppercase; font-size:.8rem; transform:rotate(3deg); }}

/* ---- the confirmation question: a ticket stub ---- */
.st-key-pending {{ background:{MUSTARD}33; border:2px dashed {INK}; border-radius:4px; padding:.8rem 1rem; }}

/* ---- login hero ---- */
.rs-hero {{ display:flex; align-items:center; gap:1.5rem; margin:1rem 0 1.5rem; }}
.rs-hero .rs-brand {{ font-size:2.6rem; }}
</style>
"""


def vinyl(*, large: bool = False, spin: bool = False) -> str:
    """An LP drawn with CSS gradients only (the .rs-disc rule): no image file, no SVG, nothing
    for the HTML sanitizer of st.html to object to. Size is a class, not an inline style.

    Note: Streamlit reloads page scripts on save but can keep this IMPORTED module cached;
    after editing style.py restart `streamlit run` (an old version kept showing otherwise)."""
    classes = 'rs-disc ' + ('rs-disc-lg' if large else 'rs-disc-sm') + (' spin' if spin else '')
    return f'<div class="{classes}" aria-hidden="true">&nbsp;</div>'     # decoration; not empty


def apply() -> None:
    """Once per run, before any page draws (st.html with only <style> takes no space)."""
    st.html(CSS)


def marquee() -> None:
    """The shop sign on top of every page."""
    st.html(f'<div class="rs-marquee">{vinyl()}<div><div class="rs-brand">Record Store Assistant</div>'
            f'<div class="rs-tagline">New · used · rare vinyl &nbsp;—&nbsp; ask the shop anything</div></div></div>'
            f'<div class="rs-stripes"></div>')


def hero() -> None:
    """The login page: a spinning record (still, if the visitor prefers reduced motion)."""
    st.html(f'<div class="rs-hero">{vinyl(large=True, spin=True)}<div><div class="rs-brand">Record Store Assistant</div>'
            f'<div class="rs-tagline">Crate-digging, with a very patient clerk</div></div></div>'
            f'<div class="rs-stripes"></div>')


def esc(value) -> str:
    """Text from the API goes into our HTML escaped (record titles are data, not markup)."""
    return html.escape(str(value), quote=True)
