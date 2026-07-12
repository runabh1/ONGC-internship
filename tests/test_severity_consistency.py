from ml.incident_manager import IncidentManager, classify_severity


def test_shared_severity_logic_is_consistent() -> None:
    mgr = IncidentManager()

    short_high_confidence = mgr.classify_severity(0.8, 95.0, 60.0, 3)
    long_high_confidence = mgr.classify_severity(0.8, 95.0, 600.0, 3)
    low_confidence = mgr.classify_severity(0.3, 60.0, 60.0, 2)

    assert short_high_confidence == 'Medium'
    assert long_high_confidence == 'High'
    assert low_confidence == 'Low'
    assert classify_severity(0.8, 95.0, 60.0, 3) == short_high_confidence
