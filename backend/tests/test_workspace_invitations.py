"""Invitation HTTP regressions on current schema; UUID fixtures roll back in kltn_test."""
import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.models import WorkspaceInvitation
from app.models.enums import InvitationStatus
from tests import test_chat_realtime as fixtures
from tests.test_channel_chat_hardening import HttpAssertions

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class InvitationTests(HttpAssertions, unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixtures.ChatRealtimeTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.ChatRealtimeTests.asyncSetUp(self)
        async with self.client() as client:
            response = await client.post('workspaces', headers=self.headers(self.sender), json={'name': 'Invitation regression'})
            self.assertEqual(response.status_code, 201)
            self.workspace_id = response.json()['data']['id']
        self.base = f'workspaces/{self.workspace_id}'

    async def invite(self, client, payload=None):
        response = await client.post(self.base + '/invitations', headers=self.headers(self.sender), json=payload or {})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        return response.json()['data']

    async def test_general_null_fields_hash_preview_accept_and_roles(self):
        async with self.client() as client:
            invite = await self.invite(client, {'invitee_user_id': None, 'expires_at': None})
            async with self.session.begin():
                record = await self.session.get(WorkspaceInvitation, UUID(invite['id']))
                self.assertIsNone(record.invitee_user_id)
                self.assertIsNone(record.expires_at)
                self.assertEqual(record.token_hash, hashlib.sha256(invite['token'].encode()).hexdigest())
                self.assertNotEqual(record.token_hash, invite['token'])
            route = 'workspace-invitations/' + invite['token']
            self.error(await client.get(route), 401, 'AUTHENTICATION_REQUIRED')
            self.error(await client.get(self.base, headers=self.headers(self.member)), 403, 'WORKSPACE_ACCESS_DENIED')
            response = await client.get(route, headers=self.headers(self.member))
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()['data']['expires_at'])
            accepted = await client.post(route + '/accept', headers=self.headers(self.member))
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()['data']['role'], 'MEMBER')
            for principal, role in ((self.sender, 'OWNER'), (self.member, 'MEMBER')):
                response = await client.get(self.base, headers=self.headers(principal))
                self.assertEqual(response.json()['data']['role'], role)
            self.error(await client.post(self.base + '/invitations', headers=self.headers(self.member), json={}), 403, 'WORKSPACE_PERMISSION_DENIED')
            for method, suffix in (('GET', ''), ('POST', '/accept'), ('POST', '/decline')):
                self.error(await client.request(method, route + suffix, headers=self.headers(self.outsider)), 409, 'INVITATION_ALREADY_USED')

    async def test_targeted_invitation_rejects_wrong_user(self):
        async with self.client() as client:
            invite = await self.invite(client, {'invitee_user_id': str(self.member.user.id)})
            route = 'workspace-invitations/' + invite['token']
            for method, suffix in (('GET', ''), ('POST', '/accept'), ('POST', '/decline')):
                self.error(await client.request(method, route + suffix, headers=self.headers(self.outsider)), 403, 'INVITATION_NOT_FOR_USER')
            self.assertEqual((await client.get(route, headers=self.headers(self.member))).status_code, 200)
            response = await client.post(route + '/accept', headers=self.headers(self.member))
            self.assertEqual(response.json()['data']['role'], 'MEMBER')

    async def test_invalid_expired_revoked_and_declined(self):
        async with self.client() as client:
            expired = await self.invite(client, {'expires_at': (datetime.now(UTC) - timedelta(seconds=1)).isoformat()})
            revoked = await self.invite(client)
            declined = await self.invite(client)
            async with self.session.begin():
                record = await self.session.get(WorkspaceInvitation, UUID(revoked['id']))
                record.status = InvitationStatus.REVOKED
            await client.post('workspace-invitations/' + declined['token'] + '/decline', headers=self.headers(self.outsider))
            for token, status, code in (
                ('invalid-token', 404, 'INVITATION_INVALID'),
                (expired['token'], 410, 'INVITATION_EXPIRED'),
                (revoked['token'], 409, 'INVITATION_ALREADY_USED'),
                (declined['token'], 409, 'INVITATION_ALREADY_USED'),
            ):
                for method, suffix in (('GET', ''), ('POST', '/accept'), ('POST', '/decline')):
                    self.error(await client.request(method, 'workspace-invitations/' + token + suffix, headers=self.headers(self.member)), status, code)

    async def test_existing_member_cannot_consume_fresh_invitation(self):
        async with self.client() as client:
            invite = await self.invite(client)
            self.error(await client.get('workspace-invitations/' + invite['token'], headers=self.headers(self.sender)), 409, 'WORKSPACE_ALREADY_MEMBER')
            async with self.session.begin():
                self.assertEqual(await self.session.scalar(select(WorkspaceInvitation.status).where(WorkspaceInvitation.id == UUID(invite['id']))), InvitationStatus.PENDING)

    async def test_no_role_override_or_unknown_create_fields(self):
        async with self.client() as client:
            self.error(await client.post(self.base + '/invitations', headers=self.headers(self.sender), json={'role': 'OWNER'}), 422, 'VALIDATION_ERROR')
