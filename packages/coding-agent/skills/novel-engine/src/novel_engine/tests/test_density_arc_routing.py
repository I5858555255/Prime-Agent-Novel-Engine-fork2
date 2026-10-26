"""Configured arc synonyms must be selected for the current project and chapter."""
import json
from novel_engine.quality import density_gate


def project(root, arc, bounds):
    path = root / 'config/leak_terms/arc.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'arc': arc, 'chapters': bounds}))
    return root


def test_density_arc_respects_configured_chapter_bounds(tmp_path):
    root = project(tmp_path, 'infant', [1, 9])
    assert density_gate.arc_for_chapter(1, root) == 'infant'
    assert density_gate.arc_for_chapter(9, root) == 'infant'
    assert density_gate.arc_for_chapter(10, root) is None


def test_density_arc_does_not_inherit_other_projects_config(tmp_path):
    first = project(tmp_path / 'first', 'infant', [1, 9])
    second = project(tmp_path / 'second', 'adult', [1, 99])
    assert density_gate.arc_for_chapter(1, first) == 'infant'
    assert density_gate.arc_for_chapter(1, second) == 'adult'
