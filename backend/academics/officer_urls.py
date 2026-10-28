from django.urls import path

from . import officer_views

urlpatterns = [
    path("", officer_views.officer_control_dashboard, name="officer_dashboard"),
    path(
        "periods/create/",
        officer_views.officer_create_period,
        name="officer_create_period",
    ),
    path(
        "periods/<int:period_id>/edit/",
        officer_views.officer_edit_period,
        name="officer_edit_period",
    ),
    path(
        "examinations/create/",
        officer_views.officer_create_examination,
        name="officer_create_examination",
    ),
    path(
        "examinations/<int:examination_id>/edit/",
        officer_views.officer_edit_examination,
        name="officer_edit_examination",
    ),
    path("rooms/create/", officer_views.officer_room_form, name="officer_create_room"),
    path(
        "rooms/<int:room_id>/edit/",
        officer_views.officer_room_form,
        name="officer_edit_room",
    ),
    path(
        "malpractice/<int:case_id>/",
        officer_views.officer_case_detail,
        name="officer_case_detail",
    ),
    path(
        "malpractice/evidence/<int:evidence_id>/",
        officer_views.officer_case_evidence,
        name="officer_case_evidence",
    ),
    path(
        "anomalies/<int:anomaly_id>/",
        officer_views.officer_anomaly_detail,
        name="officer_anomaly_detail",
    ),
    path(
        "reconciliation/run/",
        officer_views.officer_run_reconciliation,
        name="officer_run_reconciliation",
    ),
    path("sessions/", officer_views.officer_workspace, {"section": "sessions"}, name="officer_sessions"),
    path(
        "sessions/<int:session_id>/",
        officer_views.officer_session_detail,
        name="officer_session_detail",
    ),
    path(
        "reports/export/",
        officer_views.officer_report_export,
        name="officer_report_export",
    ),
    path(
        "<slug:section>/",
        officer_views.officer_workspace,
        name="officer_workspace",
    ),
]
