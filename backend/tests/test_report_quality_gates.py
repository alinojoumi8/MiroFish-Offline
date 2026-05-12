from datetime import datetime, timedelta

from app.services.report_agent import Report, ReportManager, ReportStatus


def test_validate_report_output_blocks_raw_tool_call():
    report = Report(
        report_id="report_1",
        simulation_id="sim_1",
        graph_id="graph_1",
        simulation_requirement="test",
        status=ReportStatus.COMPLETED,
        markdown_content="## Section\n\n<tool_call>{}</tool_call>",
    )

    issues = ReportManager.validate_report_output(report)

    assert any(issue["code"] == "raw_tool_call" and issue["blocking"] for issue in issues)


def test_validate_report_output_warns_about_failed_interviews():
    report = Report(
        report_id="report_1",
        simulation_id="sim_1",
        graph_id="graph_1",
        simulation_requirement="test",
        status=ReportStatus.COMPLETED,
        markdown_content="Interview API call failed: No successful interviews",
    )

    issues = ReportManager.validate_report_output(report)

    assert any(issue["code"] == "failed_interview" and not issue["blocking"] for issue in issues)


def test_validate_report_output_warns_about_repeated_section_facts_and_meta_commentary():
    repeated_fact = "TechCrunch published an internal memo showing Duolingo expected 88% of users to defect."
    report = Report(
        report_id="report_1",
        simulation_id="sim_1",
        graph_id="graph_1",
        simulation_requirement="test",
        status=ReportStatus.COMPLETED,
        markdown_content=(
            "# Report\n\n"
            "## Section One\n\n"
            "The simulation captured early pressure from competitors. "
            f"{repeated_fact}\n\n"
            "## Section Two\n\n"
            "The simulation recorded analyst skepticism. "
            f"{repeated_fact}\n"
        ),
    )

    issues = ReportManager.validate_report_output(report)

    assert any(issue["code"] == "repeated_fact" for issue in issues)
    assert any(issue["code"] == "meta_commentary" for issue in issues)


def test_clean_section_content_removes_stray_bold_artifact():
    cleaned = ReportManager._clean_section_content(
        "**\n\nThe adoption story starts with pricing pressure.",
        "Market Adoption",
    )

    assert cleaned == "The adoption story starts with pricing pressure."


def test_quality_score_marks_warning_heavy_report_for_review():
    repeated_fact = "TechCrunch published an internal memo showing Duolingo expected 88% of users to defect."
    report = Report(
        report_id="report_1",
        simulation_id="sim_1",
        graph_id="graph_1",
        simulation_requirement="test",
        status=ReportStatus.COMPLETED,
        markdown_content=(
            "# Report\n\n"
            "## Section One\n\n"
            "The simulation captured this result. "
            f"{repeated_fact} Interview API call failed: No successful interviews.\n\n"
            "## Section Two\n\n"
            "The simulation recorded the same result. "
            f"{repeated_fact}\n"
        ),
    )

    report.validation_issues = ReportManager.validate_report_output(report)
    report.quality_score = ReportManager.evaluate_report_quality(report)
    ReportManager.apply_quality_status(report)

    assert report.quality_score["score"] < ReportManager.QUALITY_REVIEW_THRESHOLD
    assert report.status == ReportStatus.NEEDS_REVIEW


def test_enrich_progress_marks_old_generating_report_stale():
    old_timestamp = (datetime.now() - timedelta(minutes=20)).isoformat()
    progress = {
        "status": "generating",
        "progress": 37,
        "message": "Generating section",
        "updated_at": old_timestamp,
    }

    enriched = ReportManager.enrich_progress(progress, stale_after_seconds=60)

    assert enriched["is_stale"] is True
    assert enriched["effective_status"] == "stale"
