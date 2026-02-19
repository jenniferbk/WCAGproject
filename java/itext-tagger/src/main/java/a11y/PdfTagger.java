package a11y;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.itextpdf.kernel.geom.Rectangle;
import com.itextpdf.kernel.pdf.*;
import com.itextpdf.kernel.pdf.tagging.*;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CLI tool that applies structure tags to a PDF based on a tagging plan.
 *
 * Uses position-based matching: the plan specifies bounding boxes for
 * each element, and iText's content stream processing locates content
 * at those positions.
 *
 * Usage: java -jar itext-tagger.jar <plan.json>
 * Output: JSON result on stdout
 */
public class PdfTagger {

    private static final double BBOX_TOLERANCE = 5.0; // points

    public static void main(String[] args) {
        Result result = new Result();
        try {
            if (args.length < 1) {
                result.success = false;
                result.errors.add("Usage: java -jar itext-tagger.jar <plan.json>");
                System.out.println(new Gson().toJson(result));
                System.exit(1);
            }

            String planJson = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
            Gson gson = new Gson();
            TaggingPlan plan = gson.fromJson(planJson, TaggingPlan.class);

            result = applyTags(plan);
        } catch (Exception e) {
            result.success = false;
            result.errors.add("Fatal error: " + e.getMessage());
        }

        Gson out = new GsonBuilder().setPrettyPrinting().create();
        System.out.println(out.toJson(result));
        System.exit(result.success ? 0 : 1);
    }

    static Result applyTags(TaggingPlan plan) {
        Result result = new Result();

        // Copy input to output
        try {
            Files.copy(
                Path.of(plan.input_path),
                Path.of(plan.output_path),
                StandardCopyOption.REPLACE_EXISTING
            );
        } catch (IOException e) {
            result.success = false;
            result.errors.add("Failed to copy input to output: " + e.getMessage());
            return result;
        }

        try (PdfDocument pdfDoc = new PdfDocument(
                new PdfReader(plan.input_path),
                new PdfWriter(plan.output_path)
        )) {
            // Enable tagged mode
            pdfDoc.setTagged();

            // Set metadata
            if (plan.metadata != null) {
                setMetadata(pdfDoc, plan.metadata, result);
            }

            // Get or create the tag structure root
            PdfStructTreeRoot structRoot = pdfDoc.getStructTreeRoot();
            if (structRoot == null) {
                result.errors.add("Could not get or create StructTreeRoot");
                result.success = false;
                return result;
            }

            // Add a document-level structure element
            PdfStructElem docElem = structRoot.addKid(
                new PdfStructElem(pdfDoc, PdfName.Document)
            );

            if (plan.elements != null) {
                // Track MCIDs per page to avoid conflicts.
                // iText's getNextMcidForPage() doesn't track manually-created MCRs,
                // so we maintain our own counter.
                Map<Integer, Integer> pageMcidCounter = new HashMap<>();

                // Group elements by page for efficient processing
                Map<Integer, List<TaggingPlan.Element>> byPage = new LinkedHashMap<>();
                for (TaggingPlan.Element elem : plan.elements) {
                    byPage.computeIfAbsent(elem.page, k -> new ArrayList<>()).add(elem);
                }

                for (Map.Entry<Integer, List<TaggingPlan.Element>> entry : byPage.entrySet()) {
                    int pageIdx = entry.getKey();
                    List<TaggingPlan.Element> pageElements = entry.getValue();

                    // iText uses 1-based page numbers
                    int pageNum = pageIdx + 1;
                    if (pageNum < 1 || pageNum > pdfDoc.getNumberOfPages()) {
                        result.warnings.add("Page " + pageNum + " out of range, skipping");
                        continue;
                    }

                    PdfPage page = pdfDoc.getPage(pageNum);

                    for (TaggingPlan.Element elem : pageElements) {
                        try {
                            tagElement(pdfDoc, docElem, page, pageNum, elem, result, pageMcidCounter);
                        } catch (Exception e) {
                            result.warnings.add(
                                "Failed to tag " + elem.type + " on page " + pageNum +
                                ": " + e.getMessage()
                            );
                        }
                    }
                }
            }

            result.success = true;
            result.output_path = plan.output_path;

        } catch (Exception e) {
            result.success = false;
            result.errors.add("Failed to process PDF: " + e.getMessage());
        }

        return result;
    }

    private static void setMetadata(PdfDocument pdfDoc, TaggingPlan.Metadata meta, Result result) {
        try {
            PdfDocumentInfo info = pdfDoc.getDocumentInfo();
            if (meta.title != null && !meta.title.isEmpty()) {
                info.setTitle(meta.title);
                // Also set display title preference
                PdfViewerPreferences prefs = pdfDoc.getCatalog().getViewerPreferences();
                if (prefs == null) {
                    prefs = new PdfViewerPreferences();
                    pdfDoc.getCatalog().setViewerPreferences(prefs);
                }
                prefs.setDisplayDocTitle(true);
                result.changes.add("Set title: " + meta.title);
            }

            if (meta.language != null && !meta.language.isEmpty()) {
                pdfDoc.getCatalog().setLang(new PdfString(meta.language));
                result.changes.add("Set language: " + meta.language);
            }
        } catch (Exception e) {
            result.warnings.add("Metadata error: " + e.getMessage());
        }
    }

    private static int getNextMcid(Map<Integer, Integer> pageMcidCounter, int pageNum) {
        int mcid = pageMcidCounter.getOrDefault(pageNum, 0);
        pageMcidCounter.put(pageNum, mcid + 1);
        return mcid;
    }

    private static void tagElement(
            PdfDocument pdfDoc,
            PdfStructElem parent,
            PdfPage page,
            int pageNum,
            TaggingPlan.Element elem,
            Result result,
            Map<Integer, Integer> pageMcidCounter
    ) {
        switch (elem.type) {
            case "heading":
                tagHeading(pdfDoc, parent, page, pageNum, elem, result, pageMcidCounter);
                break;
            case "image_alt":
                tagImageAlt(pdfDoc, parent, page, pageNum, elem, result, pageMcidCounter);
                break;
            case "table":
                tagTable(pdfDoc, parent, page, pageNum, elem, result);
                break;
            case "link":
                tagLink(pdfDoc, parent, page, pageNum, elem, result);
                break;
            default:
                result.warnings.add("Unknown element type: " + elem.type);
        }
    }

    private static void tagHeading(
            PdfDocument pdfDoc,
            PdfStructElem parent,
            PdfPage page,
            int pageNum,
            TaggingPlan.Element elem,
            Result result,
            Map<Integer, Integer> pageMcidCounter
    ) {
        int level = Math.max(1, Math.min(6, elem.level));
        String tagName = "H" + level;
        PdfStructElem headingElem = parent.addKid(new PdfStructElem(pdfDoc, new PdfName(tagName)));

        // Try to link via BDC/EMC in the content stream (same pattern as images)
        boolean linked = false;
        if (elem.bbox != null && elem.bbox.length == 4) {
            int mcid = getNextMcid(pageMcidCounter, pageNum);
            linked = linkHeadingInContentStream(pdfDoc, headingElem, page, pageNum, tagName, mcid, elem, result);
        }

        if (!linked) {
            // Fallback: inject minimal BDC/EMC + ActualText.
            // A heading struct element without BDC/EMC in the content stream is
            // rejected by accessibility checkers as an orphaned MCR.
            headingElem.put(PdfName.ActualText, new PdfString(elem.text != null ? elem.text : ""));

            try {
                int mcid = getNextMcid(pageMcidCounter, pageNum);

                int csCount = page.getContentStreamCount();
                if (csCount > 0) {
                    PdfStream cs = page.getContentStream(csCount - 1);
                    if (cs != null) {
                        byte[] bytes = cs.getBytes();
                        if (bytes != null && bytes.length > 0) {
                            String content = new String(bytes, java.nio.charset.StandardCharsets.ISO_8859_1);
                            // Append a marked content sequence with an invisible text block
                            String suffix = "\n/" + tagName + " <</MCID " + mcid + ">> BDC\nBT 0 0 Td ( ) Tj ET\nEMC\n";
                            String modified = content + suffix;
                            cs.setData(modified.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1));

                            // Create MCR linking struct element to this page/MCID
                            PdfDictionary mcrDict = new PdfDictionary();
                            mcrDict.put(PdfName.Type, PdfName.MCR);
                            mcrDict.put(PdfName.MCID, new PdfNumber(mcid));
                            mcrDict.put(PdfName.Pg, page.getPdfObject());
                            headingElem.put(PdfName.K, mcrDict);
                        }
                    }
                }
            } catch (Exception e) {
                // If content stream injection fails, heading still has ActualText
                result.warnings.add("Heading BDC injection failed: " + e.getMessage());
            }
        }

        String linkType = linked ? " (content-linked)" : " (struct-only)";
        result.changes.add(
            "Tagged heading " + tagName + " on page " + pageNum +
            ": " + truncate(elem.text, 60) + linkType
        );
        result.tags_applied++;
    }

    /**
     * Link a heading structure element to actual text content in the page's
     * content stream by injecting BDC/EMC markers around BT...ET blocks
     * whose text position falls within the heading's bounding box.
     *
     * The bbox from PyMuPDF uses origin at top-left (y increases downward),
     * but PDF content stream Tm coordinates use origin at bottom-left
     * (y increases upward). We convert using page height.
     *
     * @return true if BDC/EMC was successfully injected
     */
    private static boolean linkHeadingInContentStream(
            PdfDocument pdfDoc,
            PdfStructElem headingElem,
            PdfPage page,
            int pageNum,
            String tagName,
            int mcid,
            TaggingPlan.Element elem,
            Result result
    ) {
        try {
            // Convert PyMuPDF bbox (origin top-left) to PDF coords (origin bottom-left)
            Rectangle pageRect = page.getPageSize();
            double pageHeight = pageRect.getHeight();
            double pdfX0 = elem.bbox[0];
            double pdfY0 = pageHeight - elem.bbox[3]; // bottom of bbox
            double pdfX1 = elem.bbox[2];
            double pdfY1 = pageHeight - elem.bbox[1]; // top of bbox
            double[] pdfBbox = {pdfX0, pdfY0, pdfX1, pdfY1};

            // Find and wrap matching BT...ET blocks
            int contentStreamCount = page.getContentStreamCount();
            for (int csIdx = 0; csIdx < contentStreamCount; csIdx++) {
                PdfStream contentStream = page.getContentStream(csIdx);
                if (contentStream == null) continue;

                byte[] streamBytes = contentStream.getBytes();
                if (streamBytes == null || streamBytes.length == 0) continue;

                String content = new String(streamBytes, StandardCharsets.ISO_8859_1);

                // Find BT...ET blocks that contain text at our bbox position
                String modified = wrapMatchingTextBlocks(content, pdfBbox, tagName, mcid);
                if (modified != null) {
                    contentStream.setData(modified.getBytes(StandardCharsets.ISO_8859_1));

                    // Create MCR linking struct element to this page/MCID
                    PdfDictionary mcrDict = new PdfDictionary();
                    mcrDict.put(PdfName.Type, PdfName.MCR);
                    mcrDict.put(PdfName.MCID, new PdfNumber(mcid));
                    mcrDict.put(PdfName.Pg, page.getPdfObject());
                    headingElem.put(PdfName.K, mcrDict);

                    return true;
                }
            }
            return false;
        } catch (Exception e) {
            result.warnings.add(
                "Heading content-stream linking failed on page " + pageNum + ": " + e.getMessage()
            );
            return false;
        }
    }

    /**
     * Find BT...ET blocks in a content stream whose text matrix position
     * falls within the given bounding box, and wrap them in BDC/EMC.
     *
     * @param content   The raw content stream as a string
     * @param pdfBbox   Bounding box in PDF coordinates [x0, y0, x1, y1]
     * @param tagName   Tag name (e.g., "H1", "H2")
     * @param mcid      Marked content ID
     * @return Modified content stream, or null if no match found
     */
    private static String wrapMatchingTextBlocks(
            String content, double[] pdfBbox, String tagName, int mcid
    ) {
        Pattern btPattern = Pattern.compile("(BT\\b.*?\\bET)", Pattern.DOTALL);
        Matcher btMatcher = btPattern.matcher(content);

        int firstMatch = -1;
        int lastMatchEnd = -1;

        while (btMatcher.find()) {
            String btBlock = btMatcher.group(1);
            if (textBlockOverlapsBbox(btBlock, pdfBbox)) {
                if (firstMatch == -1) {
                    firstMatch = btMatcher.start();
                }
                lastMatchEnd = btMatcher.end();
            } else if (firstMatch >= 0) {
                // Stop once we've passed the heading region
                break;
            }
        }

        if (firstMatch >= 0 && lastMatchEnd > firstMatch) {
            String bdcPrefix = "/" + tagName + " <</MCID " + mcid + ">> BDC\n";
            String emcSuffix = "\nEMC";

            StringBuilder modified = new StringBuilder();
            modified.append(content, 0, firstMatch);
            modified.append(bdcPrefix);
            modified.append(content, firstMatch, lastMatchEnd);
            modified.append(emcSuffix);
            modified.append(content, lastMatchEnd, content.length());
            return modified.toString();
        }
        return null;
    }

    /**
     * Check whether a BT...ET block contains text positioned within a bounding box.
     * Examines Tm (text matrix) operators for absolute position.
     */
    private static boolean textBlockOverlapsBbox(String btBlock, double[] bbox) {
        double tol = BBOX_TOLERANCE;

        // Check Tm operator: a b c d tx ty Tm
        // tx,ty give the text position in PDF coordinates
        Pattern tmPattern = Pattern.compile(
            "([\\d.e+-]+)\\s+([\\d.e+-]+)\\s+([\\d.e+-]+)\\s+([\\d.e+-]+)\\s+([\\d.e+-]+)\\s+([\\d.e+-]+)\\s+Tm\\b"
        );
        Matcher tmMatcher = tmPattern.matcher(btBlock);

        while (tmMatcher.find()) {
            try {
                double tx = Double.parseDouble(tmMatcher.group(5));
                double ty = Double.parseDouble(tmMatcher.group(6));
                if (tx >= bbox[0] - tol && tx <= bbox[2] + tol &&
                    ty >= bbox[1] - tol && ty <= bbox[3] + tol) {
                    return true;
                }
            } catch (NumberFormatException ignored) {}
        }

        // Check Td/TD operators: tx ty Td (move text position).
        // Td is always relative. At BT start, text matrix = identity so (0,0).
        // We accumulate Td offsets to compute the absolute position.
        // Only use this path if no Tm was found (Tm gives definitive position).
        if (!tmMatcher.find(0)) {  // reset and check if any Tm exists
            Pattern tdPattern = Pattern.compile(
                "([\\d.e+-]+)\\s+([\\d.e+-]+)\\s+T[dD]\\b"
            );
            Matcher tdMatcher = tdPattern.matcher(btBlock);
            double absX = 0.0, absY = 0.0;
            boolean found = false;
            while (tdMatcher.find()) {
                try {
                    absX += Double.parseDouble(tdMatcher.group(1));
                    absY += Double.parseDouble(tdMatcher.group(2));
                    found = true;
                } catch (NumberFormatException ignored) {}
            }
            if (found &&
                absX >= bbox[0] - tol && absX <= bbox[2] + tol &&
                absY >= bbox[1] - tol && absY <= bbox[3] + tol) {
                return true;
            }
        }

        return false;
    }

    private static void tagImageAlt(
            PdfDocument pdfDoc,
            PdfStructElem parent,
            PdfPage page,
            int pageNum,
            TaggingPlan.Element elem,
            Result result,
            Map<Integer, Integer> pageMcidCounter
    ) {
        // Create a /Figure structure element with /Alt
        PdfStructElem figElem = parent.addKid(new PdfStructElem(pdfDoc, PdfName.Figure));

        String altText = elem.alt_text != null ? elem.alt_text : "";
        figElem.put(PdfName.Alt, new PdfString(altText));

        // Store the image xref so the Python parser can match /Figure → image
        if (elem.xref > 0) {
            figElem.put(new PdfName("A11yXref"), new PdfNumber(elem.xref));
        }

        // Try to inject BDC/EMC into the content stream to properly link
        // the /Figure tag to the actual image content
        boolean linked = false;
        if (elem.xref > 0) {
            int mcid = getNextMcid(pageMcidCounter, pageNum);
            linked = linkImageInContentStream(pdfDoc, figElem, page, pageNum, mcid, elem, result);
        }

        if (!linked) {
            // Fallback: add MCR without content stream modification.
            // Use a fresh MCID from our counter (the one allocated for linking
            // was consumed even though linking failed).
            if (elem.bbox != null && elem.bbox.length == 4) {
                int fallbackMcid = getNextMcid(pageMcidCounter, pageNum);
                addMcrToPageWithMcid(figElem, page, fallbackMcid);
            }
        }

        result.changes.add(
            "Set alt text on " + (elem.image_id != null ? elem.image_id : "image") +
            " page " + pageNum + ": " + truncate(altText, 60) +
            (linked ? " (content-linked)" : " (struct-only)")
        );
        result.tags_applied++;
    }

    /**
     * Find the image XObject name on a page that matches a given xref,
     * then inject BDC/EMC around the image's Do operator in the content stream.
     * This properly links the /Figure struct element to the actual image content.
     *
     * @return true if the content stream was successfully modified
     */
    private static boolean linkImageInContentStream(
            PdfDocument pdfDoc,
            PdfStructElem figElem,
            PdfPage page,
            int pageNum,
            int mcid,
            TaggingPlan.Element elem,
            Result result
    ) {
        try {
            // Step 1: Find the XObject name for this image's xref
            String xobjName = findXObjectNameByXref(page, elem.xref);
            if (xobjName == null) {
                result.warnings.add(
                    "Could not find XObject name for xref " + elem.xref + " on page " + pageNum
                );
                return false;
            }

            // Step 2: Create MCR in the structure element
            PdfDictionary mcrDict = new PdfDictionary();
            mcrDict.put(PdfName.Type, PdfName.MCR);
            mcrDict.put(PdfName.MCID, new PdfNumber(mcid));
            mcrDict.put(PdfName.Pg, page.getPdfObject());
            figElem.put(PdfName.K, mcrDict);

            // Step 4: Inject BDC/EMC around the image Do operator in content stream
            boolean injected = injectImageBDC(page, xobjName, mcid);
            if (!injected) {
                result.warnings.add(
                    "Could not inject BDC/EMC for /" + xobjName + " on page " + pageNum
                );
                return false;
            }

            return true;
        } catch (Exception e) {
            result.warnings.add(
                "Content stream linking failed for xref " + elem.xref +
                " on page " + pageNum + ": " + e.getMessage()
            );
            return false;
        }
    }

    /**
     * Find the XObject name (e.g., "Im0", "Image1") in the page's Resources
     * that corresponds to a given PDF object xref number.
     */
    private static String findXObjectNameByXref(PdfPage page, int targetXref) {
        PdfDictionary resources = page.getResources().getPdfObject();
        if (resources == null) return null;

        PdfDictionary xobjects = resources.getAsDictionary(PdfName.XObject);
        if (xobjects == null) return null;

        for (PdfName name : xobjects.keySet()) {
            PdfObject obj = xobjects.get(name, false); // don't dereference
            if (obj instanceof PdfIndirectReference) {
                if (((PdfIndirectReference) obj).getObjNumber() == targetXref) {
                    return name.getValue();
                }
            } else if (obj.getIndirectReference() != null) {
                if (obj.getIndirectReference().getObjNumber() == targetXref) {
                    return name.getValue();
                }
            }
        }
        return null;
    }

    /**
     * Inject /Figure BDC ... EMC markers around an image's Do operator
     * in the page's content stream.
     *
     * Looks for the pattern "/<xobjName> Do" and wraps it:
     *   /Figure <</MCID N>> BDC
     *   /<xobjName> Do
     *   EMC
     *
     * @return true if the injection was successful
     */
    private static boolean injectImageBDC(PdfPage page, String xobjName, int mcid) {
        try {
            // Get content stream(s)
            int contentStreamCount = page.getContentStreamCount();
            for (int csIdx = 0; csIdx < contentStreamCount; csIdx++) {
                PdfStream contentStream = page.getContentStream(csIdx);
                if (contentStream == null) continue;

                byte[] streamBytes = contentStream.getBytes();
                if (streamBytes == null || streamBytes.length == 0) continue;

                String content = new String(streamBytes, StandardCharsets.ISO_8859_1);

                // Look for /<xobjName> Do pattern
                // The name in PDF content streams is written as /Name
                // Pattern: optional whitespace, /Name, whitespace, Do
                String escapedName = Pattern.quote(xobjName);
                Pattern doPattern = Pattern.compile(
                    "(/" + escapedName + "\\s+Do)"
                );
                Matcher matcher = doPattern.matcher(content);

                if (matcher.find()) {
                    // Build the BDC/EMC wrapper
                    String bdcPrefix = "/Figure <</MCID " + mcid + ">> BDC\n";
                    String emcSuffix = "\nEMC";

                    StringBuilder modified = new StringBuilder();
                    modified.append(content, 0, matcher.start());
                    modified.append(bdcPrefix);
                    modified.append(matcher.group(1));
                    modified.append(emcSuffix);
                    modified.append(content, matcher.end(), content.length());

                    // Write back the modified content stream
                    byte[] newBytes = modified.toString().getBytes(StandardCharsets.ISO_8859_1);
                    contentStream.setData(newBytes);
                    return true;
                }
            }
            return false;
        } catch (Exception e) {
            return false;
        }
    }

    private static void tagTable(
            PdfDocument pdfDoc,
            PdfStructElem parent,
            PdfPage page,
            int pageNum,
            TaggingPlan.Element elem,
            Result result
    ) {
        // Create a /Table structure element
        PdfStructElem tableElem = parent.addKid(new PdfStructElem(pdfDoc, PdfName.Table));

        int headerRows = Math.max(0, elem.header_rows);
        int rowCount = 0;
        int cellCount = 0;

        if (elem.rows != null && !elem.rows.isEmpty()) {
            // Build proper /TR → /TH|/TD hierarchy
            for (int rowIdx = 0; rowIdx < elem.rows.size(); rowIdx++) {
                TaggingPlan.TableRow row = elem.rows.get(rowIdx);
                if (row == null || row.cells == null || row.cells.isEmpty()) continue;

                boolean isHeaderRow = rowIdx < headerRows;

                PdfStructElem trElem = tableElem.addKid(
                    new PdfStructElem(pdfDoc, new PdfName("TR"))
                );

                for (TaggingPlan.TableCell cell : row.cells) {
                    if (cell == null) continue;

                    PdfName cellTag = isHeaderRow ? PdfName.TH : PdfName.TD;
                    PdfStructElem cellElem = trElem.addKid(
                        new PdfStructElem(pdfDoc, cellTag)
                    );

                    // Set ActualText so screen readers can read the cell
                    String cellText = cell.text != null ? cell.text : "";
                    cellElem.put(PdfName.ActualText, new PdfString(cellText));

                    // For header cells, set Scope attribute
                    if (isHeaderRow) {
                        cellElem.put(new PdfName("Scope"), new PdfName("Column"));
                    }

                    // Handle column span
                    if (cell.grid_span > 1) {
                        cellElem.put(new PdfName("ColSpan"), new PdfNumber(cell.grid_span));
                    }

                    cellCount++;
                }
                rowCount++;
            }
        } else {
            // No row data — create minimal structure with MCR
            if (elem.bbox != null && elem.bbox.length == 4) {
                addMcrToPage(pdfDoc, tableElem, page);
            }
        }

        result.changes.add(
            "Tagged table " + (elem.table_id != null ? elem.table_id : "") +
            " on page " + pageNum + " with " + headerRows + " header row(s)" +
            " (" + rowCount + " rows, " + cellCount + " cells)"
        );
        result.tags_applied++;
    }

    private static void tagLink(
            PdfDocument pdfDoc,
            PdfStructElem parent,
            PdfPage page,
            int pageNum,
            TaggingPlan.Element elem,
            Result result
    ) {
        // Create a /Link structure element with /Alt and /ActualText
        // Note: We use struct-only approach (no BDC/EMC in content stream)
        // because link text is often embedded within larger text runs.
        // Using ActualText avoids orphaned MCR references that veraPDF flags.
        PdfStructElem linkElem = parent.addKid(new PdfStructElem(pdfDoc, PdfName.Link));

        String linkText = elem.link_text != null ? elem.link_text : "";
        linkElem.put(PdfName.Alt, new PdfString(linkText));
        linkElem.put(PdfName.ActualText, new PdfString(linkText));

        result.changes.add(
            "Set link text on " + (elem.link_id != null ? elem.link_id : "link") +
            " page " + pageNum + ": " + truncate(linkText, 60)
        );
        result.tags_applied++;
    }

    /**
     * Add a marked content reference (MCR) linking a structure element to page content.
     * This creates a new marked content ID (MCID) on the page.
     *
     * @return true if the MCR was successfully added
     */
    private static boolean addMcrToPage(
            PdfDocument pdfDoc,
            PdfStructElem structElem,
            PdfPage page
    ) {
        try {
            // Get the next MCID for this page
            PdfStructTreeRoot root = pdfDoc.getStructTreeRoot();
            int mcid = root.getNextMcidForPage(page);

            // Create MCR dictionary
            PdfDictionary mcrDict = new PdfDictionary();
            mcrDict.put(PdfName.Type, PdfName.MCR);
            mcrDict.put(PdfName.MCID, new PdfNumber(mcid));
            mcrDict.put(PdfName.Pg, page.getPdfObject());

            // Add to structure element's /K
            PdfObject existing = structElem.getK();
            if (existing == null) {
                structElem.put(PdfName.K, mcrDict);
            } else if (existing instanceof PdfArray) {
                ((PdfArray) existing).add(mcrDict);
            } else {
                PdfArray arr = new PdfArray();
                arr.add(existing);
                arr.add(mcrDict);
                structElem.put(PdfName.K, arr);
            }

            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * Add an MCR with a specific MCID to a structure element.
     */
    private static boolean addMcrToPageWithMcid(
            PdfStructElem structElem,
            PdfPage page,
            int mcid
    ) {
        try {
            PdfDictionary mcrDict = new PdfDictionary();
            mcrDict.put(PdfName.Type, PdfName.MCR);
            mcrDict.put(PdfName.MCID, new PdfNumber(mcid));
            mcrDict.put(PdfName.Pg, page.getPdfObject());

            PdfObject existing = structElem.getK();
            if (existing == null) {
                structElem.put(PdfName.K, mcrDict);
            } else if (existing instanceof PdfArray) {
                ((PdfArray) existing).add(mcrDict);
            } else {
                PdfArray arr = new PdfArray();
                arr.add(existing);
                arr.add(mcrDict);
                structElem.put(PdfName.K, arr);
            }
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private static String truncate(String s, int maxLen) {
        if (s == null) return "";
        return s.length() <= maxLen ? s : s.substring(0, maxLen) + "...";
    }

    /** Result object serialized to JSON on stdout. */
    static class Result {
        boolean success = false;
        String output_path = "";
        int tags_applied = 0;
        List<String> changes = new ArrayList<>();
        List<String> warnings = new ArrayList<>();
        List<String> errors = new ArrayList<>();
    }
}
