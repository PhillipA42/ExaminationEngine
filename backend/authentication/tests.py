import re

from django.test import Client, TestCase
from django.urls import reverse
from django.middleware.csrf import get_token

from academics.models import Course, Department, School, Student
from .models import BrowserTabSession, Role, User, UserRole


class UnifiedAuthenticationTests(TestCase):
	def setUp(self):
		self.officer = User.objects.create_user(
			username='officer001',
			email='officer001@example.test',
			password='Valid-Temp-Pass-123!',
			is_staff=True,
		)
		officer_role, _ = Role.objects.get_or_create(name=Role.EXAM_OFFICER)
		UserRole.objects.create(user=self.officer, role=officer_role)

	def test_officer_login_redirects_to_officer_dashboard(self):
		response = self.client.post(reverse('login'), {
			'username': 'officer001',
			'password': 'Valid-Temp-Pass-123!',
		})

		self.assertRedirects(response, reverse('officer_dashboard'), fetch_redirect_response=False)

	def test_login_welcome_message_is_dismissed_after_two_seconds(self):
		response = self.client.post(reverse('login'), {
			'username': 'officer001',
			'password': 'Valid-Temp-Pass-123!',
		}, follow=True)

		self.assertContains(response, 'Welcome back, officer001!')
		self.assertContains(response, 'data-auto-dismiss="2000"')

	def test_tabs_keep_separate_logins_and_logout_does_not_invalidate_other_tab(self):
		school = School.objects.create(code='AUTH', name='Authentication Test School')
		department = Department.objects.create(
			school=school,
			code='AUTH',
			name='Authentication Test Department',
		)
		course = Course.objects.create(
			department=department,
			code='AUTH',
			name='Authentication Test Course',
		)
		student_user = User.objects.create_user(
			username='student002',
			email='student002@example.test',
			password='Valid-Temp-Pass-456!',
		)
		Student.objects.create(
			user=student_user,
			registration_number='AUTH/002/26',
			course=course,
			department=department,
			school=school,
		)
		client = Client(enforce_csrf_checks=True)
		client.force_login(self.officer)
		shared_session_key = client.cookies['sessionid'].value
		tab_one = 'tab-one-session-123456789'
		tab_two = 'tab-two-session-123456789'

		def csrf_token(response):
			match = re.search(
				r'name="csrfmiddlewaretoken" value="([^"]+)"',
				response.content.decode(),
			)
			self.assertIsNotNone(match)
			return match.group(1)

		login_one = client.get(f'{reverse("login")}?__tab_session__={tab_one}')
		login_two = client.get(f'{reverse("login")}?__tab_session__={tab_two}')
		response = client.post(
			f'{reverse("login")}?__tab_session__={tab_one}',
			{
				'username': 'officer001',
				'password': 'Valid-Temp-Pass-123!',
				'csrfmiddlewaretoken': csrf_token(login_one),
				'__tab_session__': tab_one,
			},
		)
		self.assertEqual(response.status_code, 302)
		self.assertNotIn('sessionid', response.cookies)
		self.assertNotIn('csrftoken', response.cookies)
		self.assertEqual(client.cookies['sessionid'].value, shared_session_key)

		response = client.post(
			f'{reverse("login")}?__tab_session__={tab_two}',
			{
				'username': 'student002',
				'password': 'Valid-Temp-Pass-456!',
				'csrfmiddlewaretoken': csrf_token(login_two),
				'__tab_session__': tab_two,
			},
		)
		self.assertEqual(response.status_code, 302)
		self.assertNotIn('sessionid', response.cookies)
		self.assertNotIn('csrftoken', response.cookies)
		self.assertEqual(client.cookies['sessionid'].value, shared_session_key)
		self.assertIn(reverse('student_dashboard'), response['Location'])

		response_one = client.get(
			f'{reverse("officer_dashboard")}?__tab_session__={tab_one}'
		)
		response_two = client.get(f'{reverse("student_dashboard")}?__tab_session__={tab_two}')
		self.assertEqual(response_one.context['user'].username, 'officer001')
		self.assertEqual(response_two.context['user'].username, 'student002')

		response = client.post(
			f'{reverse("logout")}?__tab_session__={tab_one}',
			{
				'csrfmiddlewaretoken': csrf_token(response_one),
				'__tab_session__': tab_one,
			},
		)
		self.assertEqual(response.status_code, 302)
		self.assertNotIn('sessionid', response.cookies)
		self.assertNotIn('csrftoken', response.cookies)
		self.assertEqual(
			client.get(f'{reverse("student_dashboard")}?__tab_session__={tab_two}')
			.context['user'].username,
			'student002',
		)
		new_tab_response = client.post(
			f'{reverse("logout")}?__tab_session__={tab_two}',
			{
				'csrfmiddlewaretoken': csrf_token(response_two),
				'__tab_session__': tab_two,
			},
		)
		self.assertEqual(new_tab_response.status_code, 302)
		self.assertNotEqual(
			BrowserTabSession.objects.get(tab_id=tab_one).session_key,
			BrowserTabSession.objects.get(tab_id=tab_two).session_key,
		)

	def test_logout_is_post_only_and_clears_session(self):
		self.client.force_login(self.officer)

		get_response = self.client.get(reverse('logout'))
		self.assertEqual(get_response.status_code, 405)
		self.assertTrue('_auth_user_id' in self.client.session)

		post_response = self.client.post(reverse('logout'))
		self.assertRedirects(post_response, reverse('login'), fetch_redirect_response=False)
		self.assertNotIn('_auth_user_id', self.client.session)

	def test_login_form_issues_session_scoped_csrf_token(self):
		client = Client(enforce_csrf_checks=True)
		response = client.get(reverse('login'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'name="csrfmiddlewaretoken"')
		self.assertNotIn('csrftoken', response.cookies)
		self.assertContains(response, 'officer001')

	def test_authenticated_user_can_open_login_for_another_tab_account(self):
		self.client.force_login(self.officer)

		response = self.client.get(reverse('login'))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Sign In')

	def test_officer_visiting_student_portal_is_redirected_to_officer_dashboard(self):
		self.client.force_login(self.officer)

		response = self.client.get(reverse('student_dashboard'))

		self.assertRedirects(
			response,
			reverse('officer_dashboard'),
			fetch_redirect_response=False,
		)

	def test_browser_navigation_without_tab_id_bootstraps_a_fresh_tab_session(self):
		self.client.force_login(self.officer)

		response = self.client.get(
			reverse('student_dashboard'),
			HTTP_ACCEPT='text/html,application/xhtml+xml',
		)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, '__tab_session__')
		self.assertContains(response, 'const tabId = "')
		self.assertNotContains(response, 'A registered student profile is required')

		new_tab_response = self.client.get(
			f'{reverse("student_dashboard")}?__tab_session__=fresh-tab-session-0123456789',
			HTTP_ACCEPT='text/html,application/xhtml+xml',
		)
		self.assertEqual(new_tab_response.status_code, 302)
		self.assertIn(reverse('login'), new_tab_response['Location'])

	def test_officer_login_passes_csrf_and_routes_correctly(self):
		client = Client(enforce_csrf_checks=True)
		response = client.get(reverse('login'))
		token = get_token(response.wsgi_request)

		response = client.post(reverse('login'), {
			'csrfmiddlewaretoken': token,
			'username': 'officer001',
			'password': 'Valid-Temp-Pass-123!',
		})

		self.assertRedirects(response, reverse('officer_dashboard'), fetch_redirect_response=False)
