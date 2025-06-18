#!/usr/bin/env python3
"""
Consolidate patent files into a single PDF
"""

import os
import sys
import io
import re
from pathlib import Path
from io import BytesIO

def main():
    # First, let's check what libraries are available
    try:
        import PIL
        print("✓ PIL (Pillow) is available")
    except ImportError:
        print("✗ PIL (Pillow) is NOT available - needed for image processing")
        return
        
    try:
        import PyPDF2
        print("✓ PyPDF2 is available")
    except ImportError:
        print("✗ PyPDF2 is NOT available")
        return
        
    try:
        import reportlab
        print("✓ reportlab is available")
    except ImportError:
        print("✗ reportlab is NOT available")
        return
        
    # Check for xhtml2pdf
    xhtml2pdf_available = False
    try:
        import xhtml2pdf.pisa as pisa
        print("✓ xhtml2pdf is available - will use for HTML conversion")
        xhtml2pdf_available = True
    except ImportError:
        print("✗ xhtml2pdf is NOT available - install for better HTML conversion")
        print("  You can install it with: pip install xhtml2pdf")
        
    print("\nChecking patent directory contents...")
    patent_dir = Path("patents/example-application")
    
    if not patent_dir.exists():
        print(f"Error: Directory {patent_dir} does not exist!")
        print(f"Current directory: {os.getcwd()}")
        print(f"Available directories: {[d.name for d in Path('.').iterdir() if d.is_dir()]}")
        return
        
    # Categorize files
    tif_files = sorted(patent_dir.glob("*.TIF"))
    png_files = sorted(patent_dir.glob("*.png"))
    xml_files = sorted(patent_dir.glob("*.xml"))
    html_files = sorted(patent_dir.glob("*.html"))
    txt_files = sorted(patent_dir.glob("*.txt"))
    nb_files = sorted(patent_dir.glob("*.NB"))
    
    print(f"\nFound files:")
    print(f"  TIF images: {len(tif_files)}")
    print(f"  PNG images: {len(png_files)}")
    print(f"  XML files: {len(xml_files)}")
    print(f"  HTML files: {len(html_files)}")
    print(f"  Text files: {len(txt_files)}")
    print(f"  NB files: {len(nb_files)}")
    
    # Import necessary components
    import PyPDF2
    from PIL import Image
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    
    output_pdf_path = "consolidated_patent.pdf"
    merger = PyPDF2.PdfMerger()
    
    # Process XML content (metadata)
    if xml_files:
        print(f"\nProcessing XML metadata...")
        xml_file = xml_files[0]  # Use the first XML file
        c = canvas.Canvas("metadata.pdf", pagesize=letter)
        c.setFont("Helvetica", 12)
        
        with open(xml_file, 'r', encoding='utf-8') as f:
            xml_content = f.read()
            # Extract title and basic info
            title_match = re.search(r'<invention-title[^>]*>(.*?)</invention-title>', xml_content)
            title = title_match.group(1) if title_match else "Unknown Patent"
            
            # Write to PDF
            c.drawString(72, 750, f"Patent: {title}")
            c.drawString(72, 730, f"File: {xml_file.name}")
            c.drawString(72, 710, "Patent Metadata Summary")
            c.save()
        
        # Add metadata PDF to merger
        merger.append("metadata.pdf")
    
    # Process TIF images
    if tif_files:
        print(f"Processing {len(tif_files)} TIF images...")
        for tif_file in tif_files:
            # Convert TIF to PDF
            img = Image.open(tif_file)
            img_pdf_buffer = io.BytesIO()
            img.save(img_pdf_buffer, format='PDF')
            img_pdf_buffer.seek(0)
            merger.append(img_pdf_buffer)
    
    # Process PNG images
    if png_files:
        print(f"Processing {len(png_files)} PNG images...")
        for png_file in png_files:
            # Convert PNG to PDF
            img = Image.open(png_file)
            img_pdf_buffer = io.BytesIO()
            img.save(img_pdf_buffer, format='PDF')
            img_pdf_buffer.seek(0)
            merger.append(img_pdf_buffer)
    
    # Process Text content
    if txt_files:
        print(f"Processing {len(txt_files)} text files...")
        
        # Create document styles
        styles = getSampleStyleSheet()
        
        # Add a preformatted style for text files
        preformatted_style = ParagraphStyle(
            'Preformatted',
            parent=styles['Normal'],
            fontName='Courier',
            fontSize=9,
            leading=11,
            leftIndent=0,
            rightIndent=0,
            firstLineIndent=0,
            alignment=TA_LEFT,
            spaceBefore=0,
            spaceAfter=0,
            wordWrap='LTR',
            splitLongWords=True,
        )
        
        # Process each text file into its own PDF
        for txt_file in txt_files:
            with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                text_content = f.read()
                
                # Create a buffer for the PDF
                buffer = BytesIO()
                doc = SimpleDocTemplate(
                    buffer, 
                    pagesize=letter,
                    topMargin=72,
                    bottomMargin=72,
                    leftMargin=72,
                    rightMargin=72
                )
                
                # Create content
                content = []
                
                # Add file name as header
                content.append(Paragraph(f"<b>File: {txt_file.name}</b>", styles["Heading2"]))
                content.append(Spacer(1, 12))
                
                # Wrap text in pre tags to preserve formatting
                pre_text = f"<pre>{text_content}</pre>"
                content.append(Paragraph(pre_text, preformatted_style))
                
                # Build the PDF
                doc.build(content)
                
                # Add to merger
                buffer.seek(0)
                merger.append(buffer)
    
    # Process HTML content
    if html_files:
        print(f"Processing {len(html_files)} HTML files...")
        
        if xhtml2pdf_available:
            import xhtml2pdf.pisa as pisa
            
            for html_file in html_files:
                try:
                    with open(html_file, 'r', encoding='utf-8', errors='ignore') as f:
                        html_content = f.read()
                        
                        # Clean up common CSS issues
                        # Fix missing semicolons in CSS
                        html_content = re.sub(r'font-size:\s*(\d+%)\s+line-height', r'font-size: \1; line-height', html_content)
                        html_content = re.sub(r'(\d+%)\s+([a-zA-Z-]+:)', r'\1; \2', html_content)
                        
                        # Create a buffer for the PDF
                        buffer = BytesIO()
                        
                        # Wrap content in basic HTML structure with simple CSS
                        full_html = f"""
                        <html>
                        <head>
                            <style>
                                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                                h1 {{ color: #333; }}
                                .claim_text {{ margin-left: 30px; }}
                                .para_text {{ margin-bottom: 10px; }}
                            </style>
                        </head>
                        <body>
                            <h1>File: {html_file.name}</h1>
                            {html_content}
                        </body>
                        </html>
                        """
                        
                        # Convert HTML to PDF
                        pisa.CreatePDF(
                            BytesIO(full_html.encode('utf-8')),
                            dest=buffer
                        )
                        
                        # Add to merger
                        buffer.seek(0)
                        merger.append(buffer)
                except Exception as e:
                    print(f"  Warning: Failed to process {html_file.name} with xhtml2pdf: {e}")
                    print(f"  Falling back to simple text extraction...")
                    
                    # Fallback to simple text processing
                    with open(html_file, 'r', encoding='utf-8', errors='ignore') as f:
                        html_content = f.read()
                        
                        # Strip HTML tags for simple text extraction
                        text_content = re.sub(r'<[^>]*>', ' ', html_content)
                        text_content = re.sub(r'\s+', ' ', text_content).strip()
                        
                        # Create simple PDF
                        buffer = BytesIO()
                        c = canvas.Canvas(buffer, pagesize=letter)
                        c.setFont("Helvetica-Bold", 12)
                        c.drawString(72, 750, f"File: {html_file.name}")
                        c.setFont("Helvetica", 10)
                        
                        # Add text content
                        y_position = 720
                        words = text_content.split()
                        line = ""
                        
                        for word in words:
                            if len(line + " " + word) > 80:
                                c.drawString(72, y_position, line)
                                y_position -= 12
                                line = word
                                
                                if y_position < 50:
                                    c.showPage()
                                    y_position = 750
                            else:
                                if line:
                                    line += " "
                                line += word
                        
                        if line:
                            c.drawString(72, y_position, line)
                        
                        c.save()
                        buffer.seek(0)
                        merger.append(buffer)
        else:
            # Fallback for when xhtml2pdf is not available
            print("Using fallback HTML processing (formatting will be limited)")
            c = canvas.Canvas("html_content.pdf", pagesize=letter)
            c.setFont("Helvetica", 10)
            y_position = 750
            
            for html_file in html_files:
                with open(html_file, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()
                    
                    # Strip HTML tags for simple text extraction
                    text_content = re.sub(r'<[^>]*>', ' ', html_content)
                    text_content = re.sub(r'\s+', ' ', text_content).strip()
                    
                    # Add file name as header
                    c.setFont("Helvetica-Bold", 12)
                    c.drawString(72, y_position, f"File: {html_file.name}")
                    y_position -= 20
                    c.setFont("Helvetica", 10)
                    
                    # Split text into lines for better readability
                    lines = []
                    current_line = ""
                    
                    for word in text_content.split():
                        if len(current_line + " " + word) <= 80:
                            if current_line:
                                current_line += " "
                            current_line += word
                        else:
                            lines.append(current_line)
                            current_line = word
                    
                    if current_line:
                        lines.append(current_line)
                    
                    # Add content line by line
                    for line in lines:
                        if y_position < 50:
                            c.showPage()
                            y_position = 750
                        c.drawString(72, y_position, line)
                        y_position -= 12
                    
                    y_position -= 20
                    if y_position < 100:
                        c.showPage()
                        y_position = 750
            
            c.save()
            merger.append("html_content.pdf")
    
    # Save the consolidated PDF
    print(f"\nSaving consolidated PDF to {output_pdf_path}...")
    merger.write(output_pdf_path)
    merger.close()
    
    # Clean up temporary files
    for temp_file in ["metadata.pdf", "html_content.pdf"]:
        if os.path.exists(temp_file):
            os.remove(temp_file)
    
    print(f"PDF consolidation complete! Output saved to {output_pdf_path}")

if __name__ == "__main__":
    main()
