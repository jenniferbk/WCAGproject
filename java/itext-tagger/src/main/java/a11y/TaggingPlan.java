package a11y;

import java.util.List;

/**
 * Deserialized tagging plan from JSON. Describes all structure tags
 * to apply to a PDF via position-based matching.
 */
public class TaggingPlan {
    public String input_path;
    public String output_path;
    public Metadata metadata;
    public List<Element> elements;

    public static class Metadata {
        public String title;
        public String language;
    }

    public static class Element {
        /** "heading", "image_alt", "table", "link" */
        public String type;
        /** Heading level (1-6), only for type=heading */
        public int level;
        /** Text content for matching (headings) */
        public String text;
        /** Alt text to set (images) */
        public String alt_text;
        /** Image ID from parser */
        public String image_id;
        /** Table ID from parser */
        public String table_id;
        /** Number of header rows (tables) */
        public int header_rows;
        /** Table row data for /TR, /TH, /TD children */
        public List<TableRow> rows;
        /** 0-based page index */
        public int page;
        /** Bounding box [x0, y0, x1, y1] in PDF points */
        public double[] bbox;
        /** PDF XObject cross-reference (images) */
        public int xref;
        /** Link ID from parser (links) */
        public String link_id;
        /** Descriptive link text to set (links) */
        public String link_text;
        /** Original link URL (links) */
        public String link_url;
    }

    public static class TableRow {
        public List<TableCell> cells;
    }

    public static class TableCell {
        public String text;
        public int grid_span;
    }
}
