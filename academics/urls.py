from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SchoolViewSet, DepartmentViewSet, CourseViewSet,
    UnitViewSet, LecturerViewSet, StudentViewSet, UnitRegistrationViewSet
)

router = DefaultRouter()
router.register(r'schools', SchoolViewSet)
router.register(r'departments', DepartmentViewSet)
router.register(r'courses', CourseViewSet)
router.register(r'units', UnitViewSet)
router.register(r'lecturers', LecturerViewSet)
router.register(r'students', StudentViewSet)
router.register(r'unit-registrations', UnitRegistrationViewSet)

urlpatterns = [
    path('', include(router.urls)),
]