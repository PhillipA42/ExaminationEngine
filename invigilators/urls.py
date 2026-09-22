from django.urls import path
from . import web_views

urlpatterns = [
    # Invigilator Web Portal Endpoints
    path('login/', web_views.invigilator_login, name='invigilator_login'),
    path('logout/', web_views.invigilator_logout, name='invigilator_logout'),
    path('profile/', web_views.lecturer_profile, name='lecturer_profile'),
    path('dashboard/', web_views.invigilator_dashboard, name='invigilator_dashboard'),
    path('session/<int:duty_id>/', web_views.session_roster, name='session_roster'),
    path('session/<int:duty_id>/close/', web_views.close_session, name='close_session'),
    path('session/<int:duty_id>/checkin/', web_views.student_checkin, name='student_checkin'),
    path('session/<int:duty_id>/batch-checkin/', web_views.batch_student_checkin, name='batch_student_checkin'),
    path('session/<int:duty_id>/malpractice/', web_views.file_malpractice_report, name='file_malpractice_report'),
]