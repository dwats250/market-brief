from test_rates_module import macro_section, rates_packet
from test_pipeline import narrative
from market_brief.render import render


def test_verified_changes_have_neutral_direction_labels():
    packet = rates_packet(changes={'2Y': 2, '5Y': -6, '10Y': 0, '30Y': 7})
    _, page = render(packet, narrative())
    section = macro_section(page).split("</table>", 1)[0]
    for value, label in [('+2 bp', 'Higher'), ('−6 bp', 'Lower'), ('0 bp', 'Unchanged')]:
        assert f'<b>{value}</b><small>{label}</small>' in section
    assert 'direction-positive' not in section
    assert 'direction-negative' not in section


def test_rates_layout_phone_and_desktop(tmp_path):
    from test_cadence import phone_layout_width
    _, page = render(rates_packet(), narrative())
    for width in (320, 390, 1280):
        assert phone_layout_width(page, tmp_path, width) <= width
