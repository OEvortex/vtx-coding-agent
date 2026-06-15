from vtx.ui.floating_list import FloatingList, ListItem


def _make_items(count: int) -> list[ListItem[str]]:
    return [ListItem(value=f"v{i}", label=f"item{i}") for i in range(count)]


def test_show_resets_max_label_width_to_constructor_default() -> None:
    label = "x" * 35
    items = [ListItem(value="value", label=label)]
    floating_list: FloatingList[str] = FloatingList()

    floating_list.show(items, max_label_width=40)
    wide_render = floating_list.render().plain

    floating_list.show(items)
    default_render = floating_list.render().plain

    assert label in wide_render
    assert label not in default_render
    assert "…" in default_render
    assert default_render != wide_render


def test_move_down_wraps_to_first_item() -> None:
    floating_list: FloatingList[str] = FloatingList()
    floating_list.update_items(_make_items(3))

    floating_list.move_down()
    floating_list.move_down()
    assert floating_list.selected_index == 2

    floating_list.move_down()
    assert floating_list.selected_index == 0


def test_move_up_wraps_to_last_item() -> None:
    floating_list: FloatingList[str] = FloatingList()
    floating_list.update_items(_make_items(3))

    assert floating_list.selected_index == 0
    floating_list.move_up()
    assert floating_list.selected_index == 2


def test_move_with_no_items_is_noop() -> None:
    floating_list: FloatingList[str] = FloatingList()

    floating_list.move_up()
    floating_list.move_down()

    assert floating_list.selected_index == 0
