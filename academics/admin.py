from django.contrib import admin
from .models import School, Department, Course, Unit, Lecturer, Student, UnitRegistration

admin.site.register(School)
admin.site.register(Department)
admin.site.register(Course)
admin.site.register(Unit)
admin.site.register(Lecturer)
admin.site.register(Student)
admin.site.register(UnitRegistration)