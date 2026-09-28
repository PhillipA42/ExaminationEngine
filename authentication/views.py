from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User
from .serializers import UserSerializer, RegisterUserSerializer


def unified_login(request):
    """Single credential-based sign-in for both students and staff."""
    if request.user.is_authenticated:
        current_username = request.user.username
        if request.method == 'POST':
            submitted_username = (request.POST.get('username') or '').strip()
            if submitted_username and submitted_username != current_username:
                logout(request)
        else:
            if hasattr(request.user, 'student_profile'):
                return redirect('student_dashboard')
            if hasattr(request.user, 'lecturer_profile') or request.user.is_superuser or getattr(request.user, 'is_exam_officer', False):
                return redirect('invigilator_dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.POST.get('next') or request.GET.get('next')

            if hasattr(user, 'student_profile'):
                messages.success(request, f"Welcome, {user.get_full_name() or user.username}!")
                return redirect(next_url or 'student_dashboard')

            if hasattr(user, 'lecturer_profile') or user.is_superuser or getattr(user, 'is_exam_officer', False):
                messages.success(request, f"Welcome back, {user.get_full_name() or user.username}!")
                return redirect(next_url or 'invigilator_dashboard')

            messages.error(request, "This account is not linked to a student or lecturer profile.")
            return redirect('login')

        messages.error(request, "Invalid username or password. Please try again.")

    return render(request, 'login.html', {'next': request.GET.get('next') or request.POST.get('next')})


def unified_logout(request):
    logout(request)
    messages.info(request, "You have been logged out safely.")
    return redirect('login')

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        data['user'] = UserSerializer(self.user).data
        return data

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterUserSerializer
    permission_classes = [AllowAny]

class UserProfileView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user