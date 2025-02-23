# coding: UTF-8
"""
Tests for support views.
"""

from __future__ import absolute_import

import itertools
import json
import re
from datetime import datetime, timedelta
from uuid import uuid4

import ddt
import six
from django.contrib.auth.models import User
from django.db.models import signals
from django.urls import reverse
from mock import patch
from pytz import UTC

from common.test.utils import disable_signal
from course_modes.models import CourseMode
from course_modes.tests.factories import CourseModeFactory
from lms.djangoapps.program_enrollments.link_program_enrollments import NO_PROGRAM_ENROLLMENT_TPL
from lms.djangoapps.verify_student.models import VerificationDeadline
from student.models import ENROLLED_TO_ENROLLED, CourseEnrollment, CourseEnrollmentAttribute, ManualEnrollmentAudit
from student.roles import GlobalStaff, SupportStaffRole
from student.tests.factories import CourseEnrollmentFactory, UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class SupportViewTestCase(ModuleStoreTestCase):
    """
    Base class for support view tests.
    """

    USERNAME = "support"
    EMAIL = "support@example.com"
    PASSWORD = "support"

    def setUp(self):
        """Create a user and log in. """
        super(SupportViewTestCase, self).setUp()
        self.user = UserFactory(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.course = CourseFactory.create()
        success = self.client.login(username=self.USERNAME, password=self.PASSWORD)
        self.assertTrue(success, msg="Could not log in")


class SupportViewManageUserTests(SupportViewTestCase):
    """
    Base class for support view tests.
    """

    def setUp(self):
        """Make the user support staff"""
        super(SupportViewManageUserTests, self).setUp()
        SupportStaffRole().add_users(self.user)

    def test_get_support_form(self):
        """
        Tests Support View to return Manage User Form
        """
        url = reverse('support:manage_user')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_get_form_with_user_info(self):
        """
        Tests Support View to return Manage User Form
        with user info
        """
        url = reverse('support:manage_user_detail') + self.user.username
        response = self.client.get(url)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['username'], self.user.username)

    def test_disable_user_account(self):
        """
        Tests Support View to disable the user account
        """
        test_user = UserFactory(
            username='foobar', email='foobar@foobar.com', password='foobar'
        )
        url = reverse('support:manage_user_detail') + test_user.username
        response = self.client.post(url, data={
            'username_or_email': test_user.username
        })
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(data['success_msg'], 'User Disabled Successfully')
        test_user = User.objects.get(username=test_user.username, email=test_user.email)
        self.assertEqual(test_user.has_usable_password(), False)


@ddt.ddt
class SupportViewAccessTests(SupportViewTestCase):
    """
    Tests for access control of support views.
    """

    @ddt.data(*(
        (url_name, role, has_access)
        for (url_name, (role, has_access))
        in itertools.product((
            'support:index',
            'support:certificates',
            'support:refund',
            'support:enrollment',
            'support:enrollment_list',
            'support:manage_user',
            'support:manage_user_detail',
            'support:link_program_enrollments',
        ), (
            (GlobalStaff, True),
            (SupportStaffRole, True),
            (None, False)
        ))
    ))
    @ddt.unpack
    def test_access(self, url_name, role, has_access):
        if role is not None:
            role().add_users(self.user)

        url = reverse(url_name)
        response = self.client.get(url)

        if has_access:
            self.assertEqual(response.status_code, 200)
        else:
            self.assertEqual(response.status_code, 403)

    @ddt.data(
        "support:index",
        "support:certificates",
        "support:refund",
        "support:enrollment",
        "support:enrollment_list",
        "support:manage_user",
        "support:manage_user_detail",
        "support:link_program_enrollments",
    )
    def test_require_login(self, url_name):
        url = reverse(url_name)

        # Log out then try to retrieve the page
        self.client.logout()
        response = self.client.get(url)

        # Expect a redirect to the login page
        redirect_url = "{login_url}?next={original_url}".format(
            login_url=reverse("signin_user"),
            original_url=url,
        )
        self.assertRedirects(response, redirect_url)


class SupportViewIndexTests(SupportViewTestCase):
    """
    Tests for the support index view.
    """

    EXPECTED_URL_NAMES = [
        "support:certificates",
        "support:refund",
        "support:link_program_enrollments",
    ]

    def setUp(self):
        """Make the user support staff. """
        super(SupportViewIndexTests, self).setUp()
        SupportStaffRole().add_users(self.user)

    def test_index(self):
        response = self.client.get(reverse("support:index"))
        self.assertContains(response, "Support")

        # Check that all the expected links appear on the index page.
        for url_name in self.EXPECTED_URL_NAMES:
            self.assertContains(response, reverse(url_name))


class SupportViewCertificatesTests(SupportViewTestCase):
    """
    Tests for the certificates support view.
    """
    def setUp(self):
        """Make the user support staff. """
        super(SupportViewCertificatesTests, self).setUp()
        SupportStaffRole().add_users(self.user)

    def test_certificates_no_filter(self):
        # Check that an empty initial filter is passed to the JavaScript client correctly.
        response = self.client.get(reverse("support:certificates"))
        self.assertContains(response, "userFilter: ''")

    def test_certificates_with_user_filter(self):
        # Check that an initial filter is passed to the JavaScript client.
        url = reverse("support:certificates") + "?user=student@example.com"
        response = self.client.get(url)
        self.assertContains(response, "userFilter: 'student@example.com'")

    def test_certificates_along_with_course_filter(self):
        # Check that an initial filter is passed to the JavaScript client.
        url = reverse("support:certificates") + "?user=student@example.com&course_id=" + six.text_type(self.course.id)
        response = self.client.get(url)
        self.assertContains(response, "userFilter: 'student@example.com'")
        self.assertContains(response, "courseFilter: '" + six.text_type(self.course.id) + "'")


@ddt.ddt
class SupportViewEnrollmentsTests(SharedModuleStoreTestCase, SupportViewTestCase):
    """Tests for the enrollment support view."""

    def setUp(self):
        super(SupportViewEnrollmentsTests, self).setUp()
        SupportStaffRole().add_users(self.user)

        self.course = CourseFactory(display_name=u'teꜱᴛ')
        self.student = UserFactory.create(username='student', email='test@example.com', password='test')

        for mode in (
                CourseMode.AUDIT, CourseMode.PROFESSIONAL, CourseMode.CREDIT_MODE,
                CourseMode.NO_ID_PROFESSIONAL_MODE, CourseMode.VERIFIED, CourseMode.HONOR
        ):
            CourseModeFactory.create(mode_slug=mode, course_id=self.course.id)

        self.verification_deadline = VerificationDeadline(
            course_key=self.course.id,
            deadline=datetime.now(UTC) + timedelta(days=365)
        )
        self.verification_deadline.save()

        CourseEnrollmentFactory.create(mode=CourseMode.AUDIT, user=self.student, course_id=self.course.id)

        self.url = reverse('support:enrollment_list', kwargs={'username_or_email': self.student.username})

    def assert_enrollment(self, mode):
        """
        Assert that the student's enrollment has the correct mode.
        """
        enrollment = CourseEnrollment.get_enrollment(self.student, self.course.id)
        self.assertEqual(enrollment.mode, mode)

    @ddt.data('username', 'email')
    def test_get_enrollments(self, search_string_type):
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(len(data), 1)
        self.assertDictContainsSubset({
            'mode': CourseMode.AUDIT,
            'manual_enrollment': {},
            'user': self.student.username,
            'course_id': six.text_type(self.course.id),
            'is_active': True,
            'verified_upgrade_deadline': None,
        }, data[0])
        self.assertEqual(
            {CourseMode.VERIFIED, CourseMode.AUDIT, CourseMode.HONOR,
             CourseMode.NO_ID_PROFESSIONAL_MODE, CourseMode.PROFESSIONAL,
             CourseMode.CREDIT_MODE},
            {mode['slug'] for mode in data[0]['course_modes']}
        )

    def test_get_manual_enrollment_history(self):
        ManualEnrollmentAudit.create_manual_enrollment_audit(
            self.user,
            self.student.email,
            ENROLLED_TO_ENROLLED,
            'Financial Assistance',
            CourseEnrollment.objects.get(course_id=self.course.id, user=self.student)
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertDictContainsSubset({
            'enrolled_by': self.user.email,
            'reason': 'Financial Assistance',
        }, json.loads(response.content.decode('utf-8'))[0]['manual_enrollment'])

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_change_enrollment(self, search_string_type):
        self.assertIsNone(ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email))
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.post(url, data={
            'course_id': six.text_type(self.course.id),
            'old_mode': CourseMode.AUDIT,
            'new_mode': CourseMode.VERIFIED,
            'reason': 'Financial Assistance'
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email))
        self.assert_enrollment(CourseMode.VERIFIED)

    @ddt.data(
        ({}, r"The field \"'\w+'\" is required."),  # The double quoting goes away in Django 2.0.1
        ({'course_id': 'bad course key'}, 'Could not parse course key.'),
        ({
            'course_id': 'course-v1:TestX+T101+2015',
            'old_mode': CourseMode.AUDIT,
            'new_mode': CourseMode.VERIFIED,
            'reason': ''
        }, 'Could not find enrollment for user'),
        ({
            'course_id': None,
            'old_mode': CourseMode.HONOR,
            'new_mode': CourseMode.VERIFIED,
            'reason': ''
        }, r'User \w+ is not enrolled with mode ' + CourseMode.HONOR),
        ({
            'course_id': 'course-v1:TestX+T101+2015',
            'old_mode': CourseMode.AUDIT,
            'new_mode': CourseMode.CREDIT_MODE,
            'reason': 'Enrollment cannot be changed to credit mode'
        }, '')
    )
    @ddt.unpack
    def test_change_enrollment_bad_data(self, data, error_message):
        # `self` isn't available from within the DDT declaration, so
        # assign the course ID here
        if 'course_id' in data and data['course_id'] is None:
            data['course_id'] = six.text_type(self.course.id)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(re.match(error_message, response.content))
        self.assert_enrollment(CourseMode.AUDIT)
        self.assertIsNone(ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email))

    @disable_signal(signals, 'post_save')
    @ddt.data('honor', 'audit', 'verified', 'professional', 'no-id-professional', 'credit')
    def test_update_enrollment_for_all_modes(self, new_mode):
        """ Verify support can changed the enrollment to all available modes"""
        self.assert_update_enrollment('username', new_mode)

    @disable_signal(signals, 'post_save')
    @ddt.data('honor', 'audit', 'verified', 'professional', 'no-id-professional')
    def test_update_enrollment_for_ended_course(self, new_mode):
        """ Verify support can changed the enrollment of archived course. """
        self.set_course_end_date_and_expiry()
        self.assert_update_enrollment('username', new_mode)

    @ddt.data('username', 'email')
    def test_get_enrollments_with_expired_mode(self, search_string_type):
        """ Verify that page can get the all modes with archived course. """
        self.set_course_end_date_and_expiry()
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )
        response = self.client.get(url)
        self._assert_generated_modes(response)

    @disable_signal(signals, 'post_save')
    @ddt.data('username', 'email')
    def test_update_enrollments_with_expired_mode(self, search_string_type):
        """ Verify that enrollment can be updated to verified mode. """
        self.set_course_end_date_and_expiry()
        self.assertIsNone(ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email))
        self.assert_update_enrollment(search_string_type, CourseMode.VERIFIED)

    def _assert_generated_modes(self, response):
        """Dry method to generate course modes dict and test with response data."""
        modes = CourseMode.modes_for_course(self.course.id, include_expired=True, exclude_credit=False)
        modes_data = []
        for mode in modes:
            expiry = mode.expiration_datetime.strftime('%Y-%m-%dT%H:%M:%SZ') if mode.expiration_datetime else None
            modes_data.append({
                'sku': mode.sku,
                'expiration_datetime': expiry,
                'name': mode.name,
                'currency': mode.currency,
                'bulk_sku': mode.bulk_sku,
                'min_price': mode.min_price,
                'suggested_prices': mode.suggested_prices,
                'slug': mode.slug,
                'description': mode.description
            })

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content.decode('utf-8'))
        self.assertEqual(len(data), 1)

        self.assertEqual(
            modes_data,
            data[0]['course_modes']
        )

        self.assertEqual(
            {CourseMode.VERIFIED, CourseMode.AUDIT, CourseMode.NO_ID_PROFESSIONAL_MODE,
             CourseMode.PROFESSIONAL, CourseMode.HONOR, CourseMode.CREDIT_MODE},
            {mode['slug'] for mode in data[0]['course_modes']}
        )

    def assert_update_enrollment(self, search_string_type, new_mode):
        """ Dry method to update the enrollment and assert response."""
        self.assertIsNone(ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email))
        url = reverse(
            'support:enrollment_list',
            kwargs={'username_or_email': getattr(self.student, search_string_type)}
        )

        with patch('support.views.enrollments.get_credit_provider_attribute_values') as mock_method:
            credit_provider = (
                [u'Arizona State University'], 'You are now eligible for credit from Arizona State University'
            )
            mock_method.return_value = credit_provider
            response = self.client.post(url, data={
                'course_id': six.text_type(self.course.id),
                'old_mode': CourseMode.AUDIT,
                'new_mode': new_mode,
                'reason': 'Financial Assistance'
            })

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(ManualEnrollmentAudit.get_manual_enrollment_by_email(self.student.email))
        self.assert_enrollment(new_mode)
        if new_mode == 'credit':
            enrollment_attr = CourseEnrollmentAttribute.objects.first()
            self.assertEqual(enrollment_attr.value, six.text_type(credit_provider[0]))

    def set_course_end_date_and_expiry(self):
        """ Set the course-end date and expire its verified mode."""
        self.course.start = datetime(year=1970, month=1, day=1, tzinfo=UTC)
        self.course.end = datetime(year=1970, month=1, day=10, tzinfo=UTC)

        # change verified mode expiry.
        verified_mode = CourseMode.objects.get(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED
        )
        verified_mode.expiration_datetime = datetime(year=1970, month=1, day=9, tzinfo=UTC)
        verified_mode.save()


@ddt.ddt
class SupportViewLinkProgramEnrollmentsTests(SupportViewTestCase):
    """
    Tests for the link_program_enrollments support view.
    """
    def setUp(self):
        """Make the user support staff. """
        super(SupportViewLinkProgramEnrollmentsTests, self).setUp()
        self.url = reverse("support:link_program_enrollments")
        SupportStaffRole().add_users(self.user)
        self.program_uuid = str(uuid4())
        self.text = '0001,user-0001\n0002,user-02'

    def _assert_props(self, field_name, value, response):
        self.assertIn('"{}": "{}"'.format(field_name, value), unicode(response.content, encoding='utf-8'))

    def _assert_props_list(self, field_name, values, response):
        """
        Assert that that page is being rendered with a specific list of props
        """
        values_str = ''
        if values:
            values_str = '", "'.join(values)
            values_str = '"{}"'.format(values_str)
        self.assertIn(u'"{}": [{}]'.format(field_name, values_str), unicode(response.content, encoding='utf-8'))

    def test_get(self):
        response = self.client.get(self.url)
        self._assert_props_list('successes', [], response)
        self._assert_props_list('errors', [], response)
        self._assert_props('programUUID', '', response)
        self._assert_props('text', '', response)

    def test_invalid_uuid(self):
        response = self.client.post(self.url, data={
            'program_uuid': 'notauuid',
            'text': self.text,
        })
        self._assert_props_list('errors', [u'badly formed hexadecimal UUID string'], response)

    @ddt.unpack
    @ddt.data(
        ('program_uuid', ''),
        ('', 'text'),
        ('', ''),
    )
    def test_missing_parameter(self, program_uuid, text):
        msg = u'You must provide both a program uuid and a comma separated list of external_student_key, username'
        response = self.client.post(self.url, data={
            'program_uuid': program_uuid,
            'text': text,
        })
        self._assert_props_list('errors', [msg], response)

    @ddt.data(
        '0001,learner-01\n0002,learner-02',                                 # normal
        '0001,learner-01,apple,orange\n0002,learner-02,purple',             # extra fields
        '\t0001        ,    \t  learner-01    \n   0002 , learner-02    ',  # whitespace
    )
    @patch('support.views.program_enrollments.link_program_enrollments_to_lms_users')
    def test_text(self, text, mocked_link):
        self.client.post(self.url, data={
            'program_uuid': self.program_uuid,
            'text': text,
        })
        mocked_link.assert_called_once()
        mocked_link.assert_called_with(
            self.program_uuid,
            {
                '0001': 'learner-01',
                '0002': 'learner-02',
            }
        )

    def test_junk_text(self):
        text = 'alsdjflajsdflakjs'
        response = self.client.post(self.url, data={
            'program_uuid': self.program_uuid,
            'text': text,
        })
        msg = NO_PROGRAM_ENROLLMENT_TPL.format(program_uuid=self.program_uuid, external_student_key=text)
        self._assert_props_list('errors', [msg], response)
