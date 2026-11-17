import unittest
from unittest.mock import Mock, patch
from app.graph import driver


class Neo4jDiagnosticsChecks(unittest.TestCase):
    def test_authentication_failure_is_reported_without_credentials(self):
        class AuthenticationFailure(Exception):
            code='Neo.ClientError.Security.Unauthorized'
        connection=Mock()
        connection.verify_connectivity.side_effect=AuthenticationFailure('private-password-must-not-be-published')
        with patch.object(driver.neo4j_settings,'ENABLED',True), patch.object(driver,'get_neo4j_driver',return_value=connection):
            self.assertFalse(driver.check_neo4j_connection())
        self.assertEqual(driver.neo4j_connection_problem(),'Neo.ClientError.Security.Unauthorized')

    def test_recovery_clears_old_error(self):
        with patch.object(driver.neo4j_settings,'ENABLED',True), patch.object(driver,'get_neo4j_driver',return_value=Mock()):
            self.assertTrue(driver.check_neo4j_connection())
        self.assertIsNone(driver.neo4j_connection_problem())
