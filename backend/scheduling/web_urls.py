from django.urls import path
from . import web_views

urlpatterns = [
    path('', web_views.officer_planning_dashboard, name='officer_planning_dashboard'),
    path('period/<int:period_id>/', web_views.period_timetable_planner, name='period_timetable_planner'),
    path('period/<int:period_id>/slots/', web_views.period_time_slots, name='period_time_slots'),
    path('period/<int:period_id>/print/', web_views.master_timetable_print, name='master_timetable_print'),
    path('exam/<int:examination_id>/schedule/', web_views.manual_schedule_exam, name='manual_schedule_exam'),
]
