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
