def row_in_range(index, start, end, start_inclusive, end_inclusive):
    if start_inclusive:
        if index < start:
            return False
    elif index <= start:
        return False

    if end_inclusive:
        if index > end:
            return False
    elif index >= end:
        return False

    return True


def default_row_range(total_valid_rows):
    return {
        "start": 1,
        "start_inclusive": True,
        "end": total_valid_rows,
        "end_inclusive": True,
        "total_valid_rows": total_valid_rows,
    }


def count_rows_in_range(total_valid_rows, start, end, start_inclusive, end_inclusive):
    return sum(
        1
        for index in range(1, total_valid_rows + 1)
        if row_in_range(index, start, end, start_inclusive, end_inclusive)
    )


def parse_row_range_params(row_start, row_end, start_bound, end_bound, total_valid_rows):
    if total_valid_rows < 1:
        raise ValueError("CSV has no valid rows to process.")

    start_raw = (row_start or "").strip()
    end_raw = (row_end or "").strip()

    try:
        start = int(start_raw) if start_raw else 1
        end = int(end_raw) if end_raw else total_valid_rows
    except ValueError as exc:
        raise ValueError("Row numbers must be whole numbers.") from exc

    start_inclusive = (start_bound or "inclusive").lower() != "exclusive"
    end_inclusive = (end_bound or "inclusive").lower() != "exclusive"

    if start < 1 or end < 1:
        raise ValueError("Row numbers must be at least 1.")
    if start > total_valid_rows:
        raise ValueError(
            f"Start row cannot exceed {total_valid_rows} (valid rows in CSV)."
        )
    if end > total_valid_rows:
        raise ValueError(
            f"End row cannot exceed {total_valid_rows} (valid rows in CSV)."
        )
    if start > end:
        raise ValueError("Start row cannot be greater than end row.")

    selected = count_rows_in_range(
        total_valid_rows, start, end, start_inclusive, end_inclusive
    )
    if selected == 0:
        raise ValueError("Row range selects zero valid rows.")

    return {
        "start": start,
        "start_inclusive": start_inclusive,
        "end": end,
        "end_inclusive": end_inclusive,
        "total_valid_rows": total_valid_rows,
        "selected_row_count": selected,
    }


def filter_rows_by_range(rows, row_range):
    if not row_range:
        return rows
    return [
        row
        for row in rows
        if row_in_range(
            row.get("row_index", 0),
            row_range["start"],
            row_range["end"],
            row_range["start_inclusive"],
            row_range["end_inclusive"],
        )
    ]


def format_row_range(row_range):
    if not row_range:
        return "All rows"

    start_bracket = "[" if row_range["start_inclusive"] else "("
    end_bracket = "]" if row_range["end_inclusive"] else ")"
    return (
        f"{start_bracket}{row_range['start']}, {row_range['end']}{end_bracket} "
        f"of {row_range['total_valid_rows']}"
    )
