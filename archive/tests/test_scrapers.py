from initiative_tracker.sources.hm import parse_hm_html
from initiative_tracker.sources.lmu import parse_lmu_html
from initiative_tracker.sources.tum import parse_tum_html


def test_parse_tum_html_extracts_cards() -> None:
    html = """
    <div class='c-club'>
      <div class='c-club__content'><h4>TUM Robotics Club</h4><p>Builds robots.</p></div>
      <div class='c-club__category'><a>Technology &amp; Research</a></div>
      <div class='c-club__link'><a href='https://robotics.example'>Zum Club</a></div>
    </div>
    """
    rows = parse_tum_html(html, page_url="https://www.tum.de/community/campusleben/student-clubs-galerie")
    assert len(rows) == 1
    assert rows[0].name == "TUM Robotics Club"
    assert rows[0].external_url == "https://robotics.example"


def test_parse_lmu_html_extracts_section_tagged_links() -> None:
    html = """
    <div class='rte__content'>
      <h2>Organisationen für den Berufseinstieg</h2>
      <p><ul><li><a class='is-external' href='https://startup.example'>START Munich</a>: Entrepreneurship and technology.</li></ul></p>
    </div>
    """
    rows = parse_lmu_html(html, page_url="https://www.lmu.de/de/workspace-fuer-studierende/studieren-und-leben/studentische-initiativen")
    assert len(rows) == 1
    assert rows[0].name == "START Munich"
    assert rows[0].categories == ["Organisationen für den Berufseinstieg"]


def test_parse_hm_html_extracts_cards_and_subtitle() -> None:
    html = """
    <section>
      <h2>Studentische Projekte</h2>
      <div class='ctl_card'>
        <a href='https://hm-project.example'>
          <p>Hydro Team</p>
          <p>Shell Eco Marathon Team</p>
        </a>
      </div>
    </section>
    """
    rows = parse_hm_html(html, page_url="https://www.hm.edu/studium_1/im_studium/rund_ums_studium/aktivitaeten")
    assert len(rows) == 1
    assert rows[0].name == "Hydro Team"
    assert rows[0].description_raw == "Shell Eco Marathon Team"
