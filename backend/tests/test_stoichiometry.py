"""Сверка числительных в названии: ловим соседнюю соль по составу.

Случай, ради которого модуль написан, стоит первым тестом. Остальные —
границы, за которыми сравнение должно молчать: способов назвать вещество
больше, чем корней в таблице, и догадка здесь дороже молчания.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_stoichiometry.db")

from app.services.stoichiometry import compare_names, group_counts


def test_the_aluminium_case_from_the_demo():
    """«Дигидроксимоноацетат» против «diacetate hydroxide» — разные соли.

    10.09.2026 опознание отдало вторую как то же самое вещество: номер
    142-03-0 подтверждён страницей, и отметка «самый надёжный вариант»
    досталась соседней соли.
    """
    conflict = compare_names(
        "Дигидроксимоноацетат алюминия", "aluminum;diacetate;hydroxide"
    )
    assert conflict is not None
    assert "ацетатных групп: у вас 1, у найденного 2" in conflict
    assert "гидроксильных групп: у вас 2, у найденного 1" in conflict


def test_the_right_number_raises_no_conflict():
    """Тот же разбор на верном номере молчит."""
    assert (
        compare_names(
            "Дигидроксимоноацетат алюминия", "aluminum;acetate;dihydroxide"
        )
        is None
    )


def test_bare_root_counts_as_one():
    """Корень без числительного — одна группа, иначе сравнивать нечего."""
    assert group_counts("aluminum;acetate;dihydroxide") == {
        "acetate": 1,
        "hydroxide": 2,
    }


def test_repeated_root_is_not_summed():
    """«diacetate … acetate» — одна группа, названная дважды, а не три."""
    assert group_counts("diacetate hydroxide acetate salt") == {
        "acetate": 2,
        "hydroxide": 1,
    }


def test_nothing_in_common_stays_silent():
    """Корни не пересеклись — молчим, а не выдумываем расхождение."""
    assert compare_names("2-Этилгексанол", "2-ethylhexan-1-ol") is None


def test_unknown_substance_class_stays_silent():
    """Силиконовый сополимер таблицей корней не описывается — и молчит."""
    assert compare_names("ПЭГ-12 Диметикон", "PEG-12 Dimethicone") is None


def test_latin_multiplier_pairs_are_read_too():
    """Сравниваются два английских названия так же, как русское с английским."""
    conflict = compare_names("aluminium diacetate", "aluminium triacetate")
    assert conflict is not None
    assert "у вас 2, у найденного 3" in conflict


def test_three_answers_not_two():
    """«Сошлось» и «сравнивать нечего» — разные ответы.

    10.09.2026 на проде отметку «самый надёжный вариант» получило «Acetic
    acid, aluminum salt, hydrate (2:1:1)»: состав там записан отношением, а
    не приставками, расхождения модуль не нашёл — и «нечего сравнивать»
    прошло за «сошлось».
    """
    from app.services.stoichiometry import composition_agrees

    assert (
        composition_agrees(
            "Дигидроксимоноацетат алюминия", "aluminum;acetate;dihydroxide"
        )
        is True
    )
    assert (
        composition_agrees(
            "Дигидроксимоноацетат алюминия", "aluminum;diacetate;hydroxide"
        )
        is False
    )
    assert (
        composition_agrees(
            "Дигидроксимоноацетат алюминия", "Acetic acid, aluminum salt, hydrate"
        )
        is None
    ), "приставок нет — сравнивать нечего, и это не «сошлось»"


def test_partial_overlap_is_not_a_confirmation():
    """Совпало по одному корню из двух — это совпадение, а не проверка.

    «Aluminium acetate, basic hydrate» сходится с «дигидроксимоноацетатом»
    по ацетату (один и один) и молчит про гидроксилы, которых у введённого
    два. На проде 10.09.2026 это прошло за подтверждение состава.
    """
    from app.services.stoichiometry import composition_agrees

    assert (
        composition_agrees(
            "Дигидроксимоноацетат алюминия", "Aluminium acetate, basic hydrate"
        )
        is None
    )
