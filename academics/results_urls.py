from django.urls import path
from . import results_web_views

urlpatterns = [
    # Lecturer Workflows
    path('lecturer/', results_web_views.lecturer_results_dashboard, name='lecturer_results_dashboard'),
    path('lecturer/entry/<int:examination_id>/', results_web_views.lecturer_mark_entry, name='lecturer_mark_entry'),
    path('lecturer/upload/<int:examination_id>/', results_web_views.lecturer_bulk_upload, name='lecturer_bulk_upload'),
    path('lecturer/template/<int:examination_id>/', results_web_views.download_sample_csv, name='download_sample_csv'),

    # COD Workflows
    path('cod/', results_web_views.cod_results_dashboard, name='cod_results_dashboard'),
    path('cod/review/<int:submission_id>/', results_web_views.cod_review_submission, name='cod_review_submission'),

    # Dean Workflows
    path('dean/', results_web_views.dean_results_dashboard, name='dean_results_dashboard'),
    path('dean/review/<int:submission_id>/', results_web_views.dean_review_submission, name='dean_review_submission'),

    # Examination Officer & Final Authority Workflows
    path('officer/', results_web_views.officer_results_dashboard, name='officer_results_dashboard'),
    path('officer/review/<int:submission_id>/', results_web_views.officer_review_submission, name='officer_review_submission'),
]
