package a11y;

import com.openhtmltopdf.pdfboxout.PdfRendererBuilder;
import org.jsoup.Jsoup;
import org.jsoup.helper.W3CDom;
import org.w3c.dom.Document;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

/**
 * CLI tool that converts semantic HTML to tagged PDF/UA using OpenHTMLtoPDF.
 *
 * Usage: java -jar html-to-pdf.jar <input.html> <output.pdf>
 * Output: JSON result on stdout
 */
public class HtmlToPdfUa {

    public static void main(String[] args) {
        Result result = new Result();
        try {
            if (args.length < 2) {
                result.success = false;
                result.errors.add("Usage: java -jar html-to-pdf.jar <input.html> <output.pdf>");
                System.out.println(toJson(result));
                System.exit(1);
            }

            String htmlPath = args[0];
            String pdfPath = args[1];

            result = convert(htmlPath, pdfPath);
        } catch (Exception e) {
            result.success = false;
            result.errors.add("Fatal error: " + e.getMessage());
        }

        System.out.println(toJson(result));
        System.exit(result.success ? 0 : 1);
    }

    static Result convert(String htmlPath, String pdfPath) {
        Result result = new Result();

        // Read the HTML file
        String htmlContent;
        try {
            htmlContent = Files.readString(Path.of(htmlPath), StandardCharsets.UTF_8);
        } catch (IOException e) {
            result.success = false;
            result.errors.add("Failed to read HTML: " + e.getMessage());
            return result;
        }

        // Parse HTML with Jsoup, then convert to W3C DOM
        org.jsoup.nodes.Document jsoupDoc = Jsoup.parse(htmlContent);
        // Ensure proper structure
        jsoupDoc.outputSettings().syntax(org.jsoup.nodes.Document.OutputSettings.Syntax.xml);

        W3CDom w3cDom = new W3CDom();
        Document w3cDoc = w3cDom.fromJsoup(jsoupDoc);

        // Build PDF with OpenHTMLtoPDF
        try (OutputStream os = new FileOutputStream(pdfPath)) {
            PdfRendererBuilder builder = new PdfRendererBuilder();
            builder.useFastMode();

            // Enable PDF/UA accessibility
            builder.usePdfUaAccessibility(true);
            builder.usePdfAConformance(PdfRendererBuilder.PdfAConformance.PDFA_3_A);

            // Set the base URI for resolving relative paths (images, CSS)
            String baseUri = Path.of(htmlPath).toAbsolutePath().getParent().toUri().toString();
            builder.withW3cDocument(w3cDoc, baseUri);
            builder.toStream(os);

            builder.run();

            result.success = true;
            result.output_path = pdfPath;
            result.changes.add("Generated PDF/UA from HTML: " + pdfPath);

        } catch (Exception e) {
            result.success = false;
            result.errors.add("PDF generation failed: " + e.getMessage());
        }

        return result;
    }

    /** Simple JSON serialization (avoid Gson dependency for this small tool). */
    static String toJson(Result r) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"success\": ").append(r.success).append(",\n");
        sb.append("  \"output_path\": ").append(jsonString(r.output_path)).append(",\n");
        sb.append("  \"changes\": ").append(jsonArray(r.changes)).append(",\n");
        sb.append("  \"warnings\": ").append(jsonArray(r.warnings)).append(",\n");
        sb.append("  \"errors\": ").append(jsonArray(r.errors)).append("\n");
        sb.append("}");
        return sb.toString();
    }

    private static String jsonString(String s) {
        if (s == null) return "\"\"";
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    private static String jsonArray(List<String> items) {
        if (items == null || items.isEmpty()) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < items.size(); i++) {
            if (i > 0) sb.append(", ");
            sb.append(jsonString(items.get(i)));
        }
        sb.append("]");
        return sb.toString();
    }

    static class Result {
        boolean success = false;
        String output_path = "";
        List<String> changes = new ArrayList<>();
        List<String> warnings = new ArrayList<>();
        List<String> errors = new ArrayList<>();
    }
}
