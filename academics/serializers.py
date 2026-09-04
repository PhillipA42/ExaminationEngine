from rest_framework import serializers
from authentication.serializers import UserSerializer
from .models import School, Department, Course, Unit, Lecturer, Student, UnitRegistration

class SchoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = '__all__'

class DepartmentSerializer(serializers.ModelSerializer):
    school_name = serializers.ReadOnlyField(source='school.name')

    class Meta:
        model = Department
        fields = '__all__'

class CourseSerializer(serializers.ModelSerializer):
    department_name = serializers.ReadOnlyField(source='department.name')

    class Meta:
        model = Course
        fields = '__all__'

class UnitSerializer(serializers.ModelSerializer):
    course_name = serializers.ReadOnlyField(source='course.name')

    class Meta:
        model = Unit
        fields = '__all__'

class LecturerSerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    department_name = serializers.ReadOnlyField(source='department.name')

    class Meta:
        model = Lecturer
        fields = '__all__'

class StudentSerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    course_name = serializers.ReadOnlyField(source='course.name')
    department_name = serializers.ReadOnlyField(source='department.name')
    school_name = serializers.ReadOnlyField(source='school.name')

    class Meta:
        model = Student
        fields = '__all__'

class UnitRegistrationSerializer(serializers.ModelSerializer):
    unit_details = UnitSerializer(source='unit', read_only=True)
    student_reg_number = serializers.ReadOnlyField(source='student.registration_number')

    class Meta:
        model = UnitRegistration
        fields = '__all__'