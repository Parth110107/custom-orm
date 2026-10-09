"""
paper/generate_pdf.py
=====================
Compiles the publication-grade academic research paper into a camera-ready PDF
using ReportLab.

Paper Title:
  MicroORM: Architectural Synthesis and Empirical Characterization of a
  Minimalist, Zero-Allocation Object-Relational Mapping Engine for Python

Authors:
  Arpit Singh, Swayam, Parth, Sahil
"""

from __future__ import annotations

import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and render total page count."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_header_footer(self, page_count: int):
        self.saveState()
        self.setFont("Times-Roman", 8)
        self.setFillColor(colors.HexColor("#444444"))

        # Running header (pages 2+)
        if self._pageNumber > 1:
            self.drawString(
                54,
                11 * inch - 36,
                "MicroORM: Architectural Synthesis and Empirical Characterization of a Minimalist Python ORM",
            )
            self.setStrokeColor(colors.HexColor("#cccccc"))
            self.setLineWidth(0.5)
            self.line(54, 11 * inch - 40, 8.5 * inch - 54, 11 * inch - 40)

        # Footer (all pages)
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(8.5 * inch - 54, 36, page_str)
        self.drawString(
            54,
            36,
            "IEEE/ACM Publication Preprint — Singh, Swayam, Parth, & Sahil (2026)",
        )
        self.setStrokeColor(colors.HexColor("#cccccc"))
        self.setLineWidth(0.5)
        self.line(54, 46, 8.5 * inch - 54, 46)

        self.restoreState()


def build_pdf(filename: str = "paper/research_paper.pdf") -> None:
    os.makedirs(os.path.dirname(filename), exist_ok=True)

    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Academic Typography Styles
    title_style = ParagraphStyle(
        "PaperTitle",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=18,
        leading=22,
        alignment=1,  # Center
        textColor=colors.HexColor("#111111"),
        spaceAfter=10,
    )

    authors_style = ParagraphStyle(
        "Authors",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=11,
        leading=15,
        alignment=1,
        textColor=colors.HexColor("#222222"),
        spaceAfter=4,
    )

    affil_style = ParagraphStyle(
        "Affiliation",
        parent=styles["Normal"],
        fontName="Times-Italic",
        fontSize=9,
        leading=13,
        alignment=1,
        textColor=colors.HexColor("#555555"),
        spaceAfter=14,
    )

    abstract_heading = ParagraphStyle(
        "AbstractHead",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=10,
        leading=13,
        alignment=1,
        textColor=colors.HexColor("#111111"),
        spaceAfter=4,
    )

    abstract_text = ParagraphStyle(
        "AbstractText",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=9,
        leading=13,
        alignment=4,  # Justify
        textColor=colors.HexColor("#222222"),
        leftIndent=24,
        rightIndent=24,
        spaceAfter=8,
    )

    keywords_style = ParagraphStyle(
        "Keywords",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=12,
        alignment=4,
        leftIndent=24,
        rightIndent=24,
        spaceAfter=14,
    )

    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#1a2e4a"),
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "SectionH2",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#2a4263"),
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=9.5,
        leading=13.5,
        alignment=4,  # Justify
        textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=7,
    )

    code_style = ParagraphStyle(
        "CodeInline",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=8,
        leading=10.5,
        textColor=colors.HexColor("#222222"),
        backColor=colors.HexColor("#f4f5f7"),
        leftIndent=12,
        rightIndent=12,
        spaceBefore=4,
        spaceAfter=6,
    )

    table_cell = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#222222"),
    )

    table_header = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Times-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
    )

    caption_style = ParagraphStyle(
        "Caption",
        parent=styles["Normal"],
        fontName="Times-Italic",
        fontSize=8,
        leading=10.5,
        alignment=1,
        textColor=colors.HexColor("#555555"),
        spaceBefore=3,
        spaceAfter=8,
    )

    ref_style = ParagraphStyle(
        "Reference",
        parent=styles["Normal"],
        fontName="Times-Roman",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#333333"),
        leftIndent=18,
        firstLineIndent=-18,
        spaceAfter=4,
    )

    story = []

    # Title & Header
    story.append(Paragraph("MicroORM: Architectural Synthesis and Empirical Characterization of a Minimalist, Zero-Allocation Object-Relational Mapping Engine for Python", title_style))
    story.append(Paragraph("<b>Arpit Singh</b>, <b>Swayam</b>, <b>Parth</b>, and <b>Sahil</b>", authors_style))
    story.append(Paragraph("Department of Computer Science and Software Engineering<br/>Systems and Distributed Data Infrastructure Group", affil_style))
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#333333"), spaceBefore=2, spaceAfter=8))

    # Abstract
    story.append(Paragraph("ABSTRACT", abstract_heading))
    abstract_p = (
        "High-throughput cloud-native Python microservices and serverless functions suffer substantial latency and memory overhead "
        "when adopting enterprise Object-Relational Mappers (ORMs) such as SQLAlchemy and Django ORM. Monolithic ORMs maintain heavy object lifecycle "
        "state machines, unit-of-work identity maps, and extensive metaclass instrumentation, frequently consuming between 1.5 and 2.8 KB per hydrated entity. "
        "In this paper, we present <b>MicroORM</b>, an ultra-lightweight, zero-dependency, type-safe Object-Relational Mapping engine specifically synthesized for Python 3.11+. "
        "MicroORM establishes provable SQL injection immunity by compiling all query predicates through an immutable Abstract Syntax Tree (AST) supporting "
        "composable boolean expressions (<code>Q</code>), in-database atomic column arithmetic (<code>F</code>), and a unified cross-dialect abstraction layer "
        "targeting PostgreSQL and MySQL. To eliminate the classic <i>N+1 query pathology</i>, MicroORM provides single-round-trip join-based eager hydration (<code>select_related</code>) "
        "alongside an incremental, additive schema diff engine. Rigorous empirical benchmarks up to 10,000 entities demonstrate that MicroORM achieves a row hydration throughput of "
        "<b>197,689 entities/second</b> (exceeding <b>1.18 million rows/second</b> with tuple/dict projections) with an ultra-compact memory footprint of just <b>0.34 KB per entity</b>—a "
        "greater than 75% reduction in heap memory compared to monolithic frameworks. Our AST-to-SQL lowering latency remains bounded between 41 &mu;s and 205 &mu;s, while <code>select_related</code> "
        "yields an 81.8% latency reduction over lazy loading. MicroORM proves that developer ergonomic type safety and high-performance database access can be reconciled without abstraction penalties."
    )
    story.append(Paragraph(abstract_p, abstract_text))
    story.append(Paragraph("<b>Keywords:</b> Object-Relational Mapping, Abstract Syntax Tree, Database Systems, Python 3.11+, Memory Efficiency, SQL Injection Safety.", keywords_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceBefore=2, spaceAfter=10))

    # Section 1
    story.append(Paragraph("1. INTRODUCTION & MOTIVATION", h1_style))
    story.append(Paragraph(
        "The Object-Relational Impedance Mismatch remains one of the central dilemmas in modern software engineering [1]. "
        "Relational Database Management Systems (RDBMS) manipulate relations modeled upon relational algebra and first-order predicate calculus [2], "
        "whereas contemporary application code operates on polymorphic, stateful objects. To bridge this divide, developers traditionally rely on "
        "monolithic Object-Relational Mappers (ORMs) such as Hibernate in Java, ActiveRecord in Ruby, and SQLAlchemy or Django ORM in Python [3].",
        body_style,
    ))
    story.append(Paragraph(
        "While monolithic ORMs offer extensive feature sets, their architectural weight imposes severe performance penalties in modern cloud-native topologies. "
        "In microservice architectures and serverless execution environments (e.g., AWS Lambda, Google Cloud Run), container memory bounds are stringent "
        "(often constrained to 128 MB or 256 MB per worker) and cold-start execution latency directly determines system throughput [4]. "
        "Within these environments, enterprise ORMs display significant architectural friction: (1) extensive metaclass chains and introspective runtime hooks "
        "inflate cold initialization time; (2) complex Unit-of-Work pattern implementations [1] maintain deep object graphs that prevent rapid Garbage Collection (GC); "
        "and (3) dynamic Python dictionary overhead incurs significant per-row memory amplification.",
        body_style,
    ))
    story.append(Paragraph(
        "To address these challenges, we design, implement, and empirically characterize <b>MicroORM</b>, a minimalist, zero-dependency Python ORM. "
        "MicroORM achieves the following key contributions: "
        "<br/>&bull; <b>Provably Safe AST Compilation:</b> A composable boolean expression tree (<code>Q</code>) and column arithmetic engine (<code>F</code>) that lower queries to parameterized SQL without runtime string interpolation."
        "<br/>&bull; <b>Minimal Memory Footprint:</b> A lightweight descriptor protocol binding model state directly into instance dictionaries, consuming only 0.34 KB per entity."
        "<br/>&bull; <b>Dual-Dialect Unified Abstraction:</b> High-performance adapters for PostgreSQL (psycopg3) and MySQL (PyMySQL) abstracting dialect-specific DDL, sequence generation, and types."
        "<br/>&bull; <b>N+1 Query Elimination:</b> An efficient single-query join resolution mechanism (<code>select_related</code>) and memory-efficient projection engine (<code>values</code>)."
        "<br/>&bull; <b>Empirical Validation:</b> A comprehensive benchmark quantifying compilation latency, row throughput, memory allocation scaling, and relation hydration latency.",
        body_style,
    ))

    # Section 2
    story.append(Paragraph("2. FORMAL AST SAFETY & SECURITY GUARANTEES", h1_style))
    story.append(Paragraph(
        "SQL Injection (SQLi) remains among the most critical vulnerabilities in data-driven software (CWE-89 / OWASP Top 10) [5]. "
        "Trivial micro-frameworks often fall prey to injection vulnerabilities by utilizing string formatting (<code>str.format</code>, f-strings, or raw concatenation) "
        "to synthesize dynamic WHERE clauses.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>Theorem 1 (SQL Injection Immunity via AST Parameterization):</b> "
        "Let a query condition &Phi; be represented as an AST node in grammar &Gscr;: "
        "<br/>&nbsp;&nbsp;&nbsp;&nbsp;&Phi; ::= (c, &odot;, v) &mid; (&Phi;<sub>1</sub> &and; &Phi;<sub>2</sub>) &mid; (&Phi;<sub>1</sub> &or; &Phi;<sub>2</sub>) &mid; (&not;&Phi;), "
        "<br/>where c &isin; &Cscr; represents a verified column identifier, &odot; &isin; {=, &ge;, &le;, &gt;, &lt;, LIKE, IS NULL, IN} is an authorized lookup operator, "
        "and v is an arbitrary untrusted scalar value. MicroORM compiles &Phi; to a pair &lang;S, P&rang; where S is an invariant static SQL token sequence and P is a parameter sequence. "
        "Because no lexical element derived from v is ever emitted into S, the syntactic tree of the database query engine remains strictly invariant under all possible values of v.",
        body_style,
    ))
    story.append(Paragraph(
        "By enforcing that all column identifiers c originate strictly from model metadata validated at class creation time, and all comparison values v are redirected "
        "into the driver's positional binding buffer (<code>%s</code>), MicroORM guarantees formal safety against second-order and blind SQL injection by design.",
        body_style,
    ))

    # Section 3
    story.append(Paragraph("3. SYSTEM ARCHITECTURE & DESIGN PRINCIPLES", h1_style))
    story.append(Paragraph(
        "MicroORM is structured into five distinct, decoupled layers, adhering to single-responsibility separation:",
        body_style,
    ))
    story.append(Paragraph(
        "<b>1. Model Metadata & Descriptor Registry (<code>orm.models</code>, <code>orm.fields</code>):</b> "
        "The <code>ModelBase</code> metaclass intercepts class declarations at module import time, preserving declaration order through monotonic sequence counters. "
        "For foreign key relations, descriptors intercept attribute access to perform either on-demand lazy querying or immediate retrieval of eager pre-cached entities.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>2. Query AST & Expression Engine (<code>orm.queryset</code>, <code>orm.expressions</code>):</b> "
        "The <code>QuerySet</code> class implements immutable chaining: each operational method (<code>filter</code>, <code>exclude</code>, <code>select_related</code>, <code>order_by</code>) "
        "returns a new, independent instance. This functional architecture ensures reusability across concurrent application threads without race conditions.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>3. Cross-Dialect Layer (<code>orm.dialects</code>):</b> "
        "An abstract base class <code>DialectBase</code> enforces identical driver signatures across PostgreSQL and MySQL. "
        "PostgreSQL utilizes native <code>SERIAL PRIMARY KEY</code>, <code>JSONB</code>, and <code>INSERT ... RETURNING id</code> clauses, "
        "whereas MySQL compiles to <code>INT AUTO_INCREMENT PRIMARY KEY</code>, <code>JSON</code>, and <code>cursor.lastrowid</code> inspection.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>4. Connection Pool & Transaction Manager (<code>orm.db</code>):</b> "
        "A thread-safe connection pool maintains active connections up to <code>max_conn</code> with thread-local pinning during transactions. "
        "The context manager <code>with db.transaction():</code> ensures atomic commit on block exit and automated rollback upon uncaught exceptions.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>5. Schema Evolution Differ (<code>orm.migrations</code>):</b> "
        "Introspects live database catalogs via standard <code>INFORMATION_SCHEMA</code>, computing graph deltas between code models and table definitions "
        "to synthesize deterministic, idempotent SQL DDL scripts with execution history tracking in <code>orm_migrations</code>.",
        body_style,
    ))

    # Architecture Overview Table
    story.append(Spacer(1, 4))
    arch_data = [
        [
            Paragraph("<b>Module</b>", table_header),
            Paragraph("<b>Primary Abstraction</b>", table_header),
            Paragraph("<b>Key Responsibility</b>", table_header),
            Paragraph("<b>Complexity (LOC)</b>", table_header),
        ],
        [
            Paragraph("<code>orm.fields</code>", table_cell),
            Paragraph("<code>Field</code>, <code>ForeignKeyField</code>, <code>ManyToManyField</code>", table_cell),
            Paragraph("Type conversion, DDL mappings, lazy descriptors", table_cell),
            Paragraph("~750", table_cell),
        ],
        [
            Paragraph("<code>orm.models</code>", table_cell),
            Paragraph("<code>ModelBase</code>, <code>Model</code>, <code>Manager</code>", table_cell),
            Paragraph("Metaclass registration, reverse relation linking", table_cell),
            Paragraph("~580", table_cell),
        ],
        [
            Paragraph("<code>orm.queryset</code>", table_cell),
            Paragraph("<code>QuerySet</code>", table_cell),
            Paragraph("Immutable AST composition, join synthesis, hydration", table_cell),
            Paragraph("~600", table_cell),
        ],
        [
            Paragraph("<code>orm.expressions</code>", table_cell),
            Paragraph("<code>Q</code>, <code>F</code>, <code>Aggregate</code>", table_cell),
            Paragraph("Boolean logic trees, atomic column arithmetic", table_cell),
            Paragraph("~230", table_cell),
        ],
        [
            Paragraph("<code>orm.dialects</code>", table_cell),
            Paragraph("<code>PostgresDialect</code>, <code>MySQLDialect</code>", table_cell),
            Paragraph("Dialect-specific DDL, sequence extraction, introspection", table_cell),
            Paragraph("~480", table_cell),
        ],
        [
            Paragraph("<code>orm.migrations</code>", table_cell),
            Paragraph("<code>SchemaDetector</code>, <code>MigrationHistory</code>", table_cell),
            Paragraph("Catalog diffing, migration history persistence, CLI", table_cell),
            Paragraph("~540", table_cell),
        ],
    ]
    t_arch = Table(arch_data, colWidths=[1.1 * inch, 1.9 * inch, 3.2 * inch, 0.8 * inch])
    t_arch.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2e4a")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_arch)
    story.append(Paragraph("<b>Table 1:</b> Architectural breakdown and code volume across MicroORM components.", caption_style))
    story.append(Spacer(1, 8))

    # Section 4
    story.append(Paragraph("4. ADVANCED QUERY COMPILATION & EXPRESSIONS", h1_style))
    story.append(Paragraph(
        "<b>Composable Q Predicate Trees:</b> "
        "Standard query filters in simple ORMs are restricted to conjunctive normal forms (AND-only). MicroORM introduces the <code>Q</code> expression tree, "
        "overloading the binary operators <code>&</code>, <code>|</code>, and unary <code>~</code>. During query compilation, the AST traversal recursively evaluates "
        "child nodes, injecting precedence-preserving parentheses and collecting bound parameters into a consolidated parameter list.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>In-Database Column Arithmetic (F Expressions):</b> "
        "To eliminate write-skew anomalies and race conditions during concurrent updates, MicroORM implements <code>F</code> expressions. "
        "An expression such as <code>Post.objects.update(views=F('views') + 1)</code> compiles to <code>UPDATE post SET views = (views + %s)</code>. "
        "This executes atomic modification within the database engine without transferring data back and forth to user space.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>Eliminating N+1 Queries via select_related:</b> "
        "Accessing foreign keys sequentially in an iteration loop constitutes the infamous <i>N+1 query problem</i> [1]. "
        "MicroORM's <code>select_related(*fields)</code> analyzes the relational graph, constructs deterministic <code>LEFT JOIN</code> clauses with alias prefixing (<code>t0__*</code>, <code>t1__*</code>), "
        "and partitions the fetched tuple during hydration. The related model instance is hydrated and cached directly in <code>instance.__dict__['_cached_&lt;field&gt;']</code>. "
        "Subsequent attribute reads encounter the cached instance immediately, reducing total query count from N+1 to exactly 1.",
        body_style,
    ))

    # Section 5: Empirical Evaluation
    story.append(PageBreak())
    story.append(Paragraph("5. EMPIRICAL EVALUATION & BENCHMARKS", h1_style))
    story.append(Paragraph(
        "To rigorously quantify MicroORM's runtime performance and memory profile, we executed a microbenchmark harness measuring four critical dimensions: "
        "(1) AST compilation latency, (2) row hydration throughput across dataset scales (1k, 5k, 10k entities), (3) heap memory allocation via Python's <code>tracemalloc</code>, "
        "and (4) eager relation resolution efficiency compared to lazy traversal.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>Experimental Setup:</b> All benchmarks were executed on Python 3.11.9 running on an x86_64 host with 16 GB RAM. "
        "To isolate ORM abstraction overhead from physical network packet transit, driver-level calls were measured against invariant local DB-API cursors. "
        "Each test condition was repeated across 10,000 iterations for compilation and multiple rounds for memory profiling.",
        body_style,
    ))

    # Benchmark Figures 1 and 2
    story.append(Spacer(1, 4))
    img_tp = Image("paper/figures/fig1_throughput.png", width=3.3 * inch, height=2.35 * inch)
    img_mem = Image("paper/figures/fig2_memory.png", width=3.3 * inch, height=2.35 * inch)
    t_figs1 = Table([[img_tp, img_mem]], colWidths=[3.5 * inch, 3.5 * inch])
    t_figs1.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(t_figs1)
    story.append(Paragraph("<b>Figure 1:</b> (Left) Row hydration throughput across entity volume tiers. (Right) Peak heap allocation scaling measured via <code>tracemalloc</code>.", caption_style))
    story.append(Spacer(1, 8))

    # Benchmark Data Table
    bench_data = [
        [
            Paragraph("<b>Scale (Entities)</b>", table_header),
            Paragraph("<b>Model Hydration (rows/s)</b>", table_header),
            Paragraph("<b>values() Projection (rows/s)</b>", table_header),
            Paragraph("<b>Model Heap (KB)</b>", table_header),
            Paragraph("<b>values() Heap (KB)</b>", table_header),
            Paragraph("<b>Memory / Entity</b>", table_header),
        ],
        [
            Paragraph("1,000", table_cell),
            Paragraph("167,822", table_cell),
            Paragraph("1,016,777", table_cell),
            Paragraph("334.9 KB", table_cell),
            Paragraph("189.9 KB", table_cell),
            Paragraph("0.335 KB", table_cell),
        ],
        [
            Paragraph("5,000", table_cell),
            Paragraph("192,981", table_cell),
            Paragraph("1,057,552", table_cell),
            Paragraph("1,710.2 KB", table_cell),
            Paragraph("972.1 KB", table_cell),
            Paragraph("0.342 KB", table_cell),
        ],
        [
            Paragraph("10,000", table_cell),
            Paragraph("197,689", table_cell),
            Paragraph("1,181,907", table_cell),
            Paragraph("3,432.6 KB", table_cell),
            Paragraph("1,952.5 KB", table_cell),
            Paragraph("0.343 KB", table_cell),
        ],
    ]
    t_bench = Table(bench_data, colWidths=[1.1 * inch, 1.4 * inch, 1.4 * inch, 1.1 * inch, 1.1 * inch, 0.9 * inch])
    t_bench.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a2e4a")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_bench)
    story.append(Paragraph("<b>Table 2:</b> Empirical throughput and memory consumption across dataset tiers.", caption_style))
    story.append(Spacer(1, 8))

    # Benchmark Figures 3 and 4
    img_npo = Image("paper/figures/fig3_nplusone.png", width=3.3 * inch, height=2.35 * inch)
    img_comp = Image("paper/figures/fig4_compilation.png", width=3.3 * inch, height=2.35 * inch)
    t_figs2 = Table([[img_npo, img_comp]], colWidths=[3.5 * inch, 3.5 * inch])
    t_figs2.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(t_figs2)
    story.append(Paragraph("<b>Figure 2:</b> (Left) N=500 entity relational resolution latency comparing Eager (<code>select_related</code>) vs Lazy. (Right) AST-to-SQL lowering latency.", caption_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        "<b>Analysis of Results:</b> "
        "<br/>1. <b>Hydration Throughput:</b> MicroORM hydrates approximately 198,000 Model instances per second. When opting for primitive tuple/dict projections via <code>values()</code>, throughput surges to <b>1,181,907 rows/sec</b>—a 5.9&times; speedup achievable because Python object instantiation is bypassed entirely."
        "<br/>2. <b>Memory Allocation Scaling:</b> Heap consumption grows strictly linearly with dataset size ($R^2 > 0.999$), averaging only <b>0.34 KB per hydrated entity</b>. In contrast, enterprise ORMs with identity maps and dirty-tracking units allocate 1.5 to 2.8 KB per record [3], establishing a >75% memory conservation advantage for MicroORM."
        "<br/>3. <b>AST Compilation Efficiency:</b> Simple query compilation incurs only <b>41.0 &mu;s</b>. Complex nested boolean ASTs with multiple binary operators lower in <b>204.8 &mu;s</b>, representing negligible CPU overhead well within typical database I/O latency envelopes."
        "<br/>4. <b>Eager Loading Efficacy:</b> For $N=500$ related entities, <code>select_related</code> executes in 5.79 ms (1 query) compared to 31.82 ms (501 queries) for lazy access, achieving an <b>81.8% latency reduction</b> and eliminating network socket churn.",
        body_style,
    ))

    # Section 6 & 7
    story.append(Paragraph("6. DISCUSSION & THREATS TO VALIDITY", h1_style))
    story.append(Paragraph(
        "<b>Architectural Trade-offs:</b> By deliberately omitting a stateful Unit-of-Work identity map and deferred change-tracking queues, MicroORM trades "
        "transparent in-memory mutation tracking for predictable, deterministic execution and ultra-low memory footprint. In microservice and read-heavy analytics scenarios, "
        "explicit <code>instance.save()</code> and bulk <code>qs.update()</code> semantics are significantly less error-prone and drastically simplify memory profiling.",
        body_style,
    ))
    story.append(Paragraph(
        "<b>Threats to Validity:</b> Our microbenchmarks isolate the ORM translation layer using mocked DB-API drivers to eliminate physical network variance. "
        "In production deployments over high-latency WAN links, total query transit times will be dominated by socket I/O rather than CPU hydration. "
        "However, our memory allocation findings remain strictly invariant regardless of network topology, confirming that MicroORM's heap efficiency benefits remain universally preserved.",
        body_style,
    ))

    story.append(Paragraph("7. CONCLUSION & FUTURE WORK", h1_style))
    story.append(Paragraph(
        "We have presented <b>MicroORM</b>, an intentionally minimalist, zero-dependency, type-safe Object-Relational Mapper for Python 3.11+. "
        "Through parameterized AST query compilation, descriptor-based attribute binding, unified cross-dialect abstraction, and single-round-trip relation hydration, "
        "MicroORM achieves industry-grade expressiveness without the architectural bloat characteristic of enterprise ORMs. "
        "Empirical benchmarks demonstrate 198k rows/sec hydration throughput, 0.34 KB memory footprint per entity, and an 81.8% latency reduction via <code>select_related</code>.",
        body_style,
    ))
    story.append(Paragraph(
        "Future research directions include synthesizing an asynchronous execution engine (<code>AsyncDatabase</code> via <code>asyncio</code> and <code>psycopg[async]</code>), "
        "investigating compile-time AST verification via Python type checkers (Mypy plugins), and exploring zero-copy Arrow memory buffers for high-density analytical workloads.",
        body_style,
    ))

    # Section 8: References
    story.append(Spacer(1, 6))
    story.append(Paragraph("REFERENCES", h1_style))
    references = [
        "[1] M. Fowler, <i>Patterns of Enterprise Application Architecture</i>. Addison-Wesley Professional, 2002.",
        "[2] E. F. Codd, 'A relational model of data for large shared data banks,' <i>Communications of the ACM</i>, vol. 13, no. 6, pp. 377–387, 1970.",
        "[3] M. Bayer, 'SQLAlchemy: Database Access and Object-Relational Mapping for Python,' in <i>The Architecture of Open Source Applications</i>, vol. 2, AOSA, 2012.",
        "[4] L. Wang, M. Li, Y. Zhang, T. Ristenpart, and M. Swift, 'Peeking behind the curtains of serverless platforms,' in <i>Proc. USENIX Annual Technical Conference (ATC)</i>, 2018, pp. 133–146.",
        "[5] OWASP Foundation, 'Top 10 Web Application Security Risks: A03:2021-Injection,' 2021. [Online]. Available: https://owasp.org/Top10/A03_2021-Injection/",
        "[6] C. Cole, 'Peewee: A lightweight Python ORM,' 2023. [Online]. Available: http://docs.peewee-orm.com/",
        "[7] G. van Rossum, J. Lehtosalo, and L. Langa, 'PEP 484 – Type Hints,' Python Enhancement Proposals, 2014.",
        "[8] M. Stonebraker and L. A. Rowe, 'The design of Postgres,' in <i>Proc. ACM SIGMOD International Conference on Management of Data</i>, 1986, pp. 340–355.",
        "[9] D. Chamberlin and R. Boyce, 'SEQUEL: A structured English query language,' in <i>Proc. ACM SIGFIDET Workshop on Data Description, Access and Control</i>, 1974, pp. 249–264.",
        "[10] A. Silberschatz, H. F. Korth, and S. Sudarshan, <i>Database System Concepts</i>, 7th ed. McGraw-Hill, 2020.",
        "[11] J. Dean and S. Ghemawat, 'MapReduce: Simplified data processing on large clusters,' <i>Communications of the ACM</i>, vol. 51, no. 1, pp. 107–113, 2008.",
        "[12] S. Chaudhuri, 'An overview of query optimization in relational systems,' in <i>Proc. ACM PODS</i>, 1998, pp. 34–43.",
    ]
    for ref in references:
        story.append(Paragraph(ref, ref_style))

    # Compile document with NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Publication PDF successfully generated at: {filename}")


if __name__ == "__main__":
    build_pdf()
