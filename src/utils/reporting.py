"""Service for generating professional PDF reports from Markdown content."""

import os
from fpdf import FPDF
from datetime import datetime

def clean_text(text: str) -> str:
    """Replace non-latin-1 characters with ASCII equivalents for basic font support."""
    if not text:
        return ""
    replacements = {
        "—": "-",    # em-dash
        "–": "-",    # en-dash
        "“": '"',    # smart open quote
        "”": '"',    # smart close quote
        "‘": "'",    # smart open single quote
        "’": "'",    # smart close single quote
        "…": "...",  # ellipsis
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # Final safety: encode to latin-1 and back to strip any remaining non-compatible chars
    return text.encode('latin-1', 'replace').decode('latin-1').replace('?', '-')

class ReportPDF(FPDF):
    def header(self):
        self.set_font('helvetica', 'B', 15)
        # Using a plain ASCII string literal
        header_title = "Macro Intelligence Platform - Research Report"
        self.cell(0, 10, header_title, 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('helvetica', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()} | Generated on {datetime.now().strftime("%Y-%m-%d")}', 0, 0, 'C')

def generate_pdf_report(title: str, content: str, output_path: str) -> str:
    """Convert Markdown research content to a PDF file."""
    pdf = ReportPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("helvetica", "B", 16)
    pdf.multi_cell(0, 10, clean_text(title))
    pdf.ln(10)
    
    # Body
    pdf.set_font("helvetica", "", 12)
    # Simple markdown-to-pdf conversion
    text = content.replace("###", "").replace("##", "").replace("**", "").replace("*", "")
    pdf.multi_cell(0, 8, clean_text(text))
    
    pdf.output(output_path)
    return output_path
