def _split_into_semantic_units(text: str) -> tuple[list[str], list[tuple[int, int, int]]]:
    """
    Split text into semantic units, preserving structural elements like tables,
    code blocks, lists, and document sections.

    Strategy:
    - Group content by sections (defined by headings like "# Step X")
    - Keep tables, code blocks intact
    - Keep related list items together
    - Each section becomes one or more semantic units

    Returns:
        Tuple of (content_units, unit_boundaries)
        unit_boundaries: list of (start_char, end_char, unit_index)
    """
    import re

    # Split text into lines
    if '\r\n' in text:
        lines = text.split('\r\n')
        line_ending = '\r\n'
    else:
        lines = text.split('\n')
        line_ending = '\n'

    def get_line_pos(idx: int) -> int:
        """Get character position of line start"""
        return sum(len(lines[j]) + len(line_ending) for j in range(idx))

    def is_heading(line_idx: int) -> bool:
        """Check if line is a heading"""
        if line_idx >= len(lines):
            return False
        line = lines[line_idx]
        # Markdown heading: # Heading
        if re.match(r'^#{1,6}\s', line.strip()):
            return True
        # Setext heading: Heading\n=====
        if line_idx + 1 < len(lines):
            next_line = lines[line_idx + 1]
            if line.strip() and next_line.strip() and all(c in '=-' for c in next_line.strip()):
                return True
        return False

    def collect_table(start_idx: int) -> tuple[int, list[str]]:
        """Collect HTML or Markdown table"""
        line = lines[start_idx]
        table_lines = [line]

        if line.strip().lower().startswith('<table'):
            i = start_idx + 1
            while i < len(lines):
                table_lines.append(lines[i])
                if '</table>' in lines[i].lower():
                    i += 1
                    break
                i += 1
            return i, table_lines
        elif line.strip().startswith('|'):
            i = start_idx + 1
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i])
                i += 1
            return i, table_lines

        return start_idx + 1, table_lines

    units = []
    boundaries = []

    # Group by sections
    current_section_lines = []
    current_section_start = 0
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip leading empty lines
        if not line.strip() and not current_section_lines:
            i += 1
            continue

        # Check for heading - start of new section
        if is_heading(i):
            # Save previous section
            if current_section_lines:
                start = get_line_pos(current_section_start)
                end = get_line_pos(i - 1) + len(lines[i - 1]) if i > 0 else start
                content = line_ending.join(current_section_lines).strip()
                if content:
                    units.append(content)
                    boundaries.append((start, end, len(units) - 1))

            # Start new section with this heading
            current_section_lines = [line]
            current_section_start = i
            i += 1
            continue

        # Check for HTML table (inline, might be in a section)
        if line.strip().lower().startswith('<table'):
            end_idx, table_lines = collect_table(i)
            current_section_lines.extend(table_lines)
            i = end_idx
            continue

        # Check for Markdown table
        if line.strip().startswith('|') and '|' in line[1:]:
            end_idx, table_lines = collect_table(i)
            current_section_lines.extend(table_lines)
            i = end_idx
            continue

        # Check for code block
        if line.strip().startswith('```'):
            code_lines = [line]
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                code_lines.append(lines[i])
                i += 1
            current_section_lines.extend(code_lines)
            continue

        # Regular line - add to current section
        current_section_lines.append(line)
        i += 1

    # Don't forget the last section
    if current_section_lines:
        start = get_line_pos(current_section_start)
        if len(lines) > 0:
            end = get_line_pos(len(lines) - 1) + len(lines[-1])
        else:
            end = start
        content = line_ending.join(current_section_lines).strip()
        if content:
            units.append(content)
            boundaries.append((start, end, len(units) - 1))

    # Post-process: split very large sections
    MAX_UNIT_SIZE = 2000
    final_units = []
    final_boundaries = []

    for unit, (start, end, _) in zip(units, boundaries):
        if len(unit) <= MAX_UNIT_SIZE:
            final_units.append(unit)
            final_boundaries.append((start, end, len(final_units) - 1))
        else:
            # Split large section at paragraph boundaries
            paragraphs = unit.split('\n\n')
            current_chunk = []
            current_size = 0
            chunk_start = start

            for para in paragraphs:
                para_size = len(para) + 2  # +2 for \n\n

                if current_size + para_size > MAX_UNIT_SIZE and current_chunk:
                    # Save current chunk
                    chunk_content = '\n\n'.join(current_chunk)
                    chunk_end = chunk_start + len(chunk_content)
                    final_units.append(chunk_content)
                    final_boundaries.append((chunk_start, chunk_end, len(final_units) - 1))

                    # Start new chunk
                    current_chunk = [para]
                    current_size = para_size
                    chunk_start = chunk_end + 2
                else:
                    current_chunk.append(para)
                    current_size += para_size

            # Save last chunk
            if current_chunk:
                chunk_content = '\n\n'.join(current_chunk)
                chunk_end = start + len(unit)
                final_units.append(chunk_content)
                final_boundaries.append((chunk_start, chunk_end, len(final_units) - 1))

    return final_units, final_boundaries
