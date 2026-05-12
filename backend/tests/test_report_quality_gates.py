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
