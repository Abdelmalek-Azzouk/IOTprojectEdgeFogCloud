from edge.alert_manager import AlertManager


def test_levels():
    am = AlertManager()
    assert am.evaluate_single({"prediction": "normal", "confidence": 0.9, "fatigue_score": 0.1}) is None
    assert am.evaluate_single({"prediction": "alert", "confidence": 0.9, "fatigue_score": 0.65})[0] == "warning"
    assert am.evaluate_single({"prediction": "drowsy", "confidence": 0.9, "fatigue_score": 0.7})[0] == "critical"
    assert am.evaluate_single({"prediction": "alert", "confidence": 0.9, "fatigue_score": 0.9})[0] == "critical"


def test_asleep_flag():
    am = AlertManager()
    p = {"prediction": "drowsy", "confidence": 0.95, "fatigue_score": 0.8, "extra": {"asleep": True}}
    res = am.evaluate(p)
    assert len(res) >= 1
    assert res[0][0] == "critical"
    assert "asleep" in res[0][1]


def test_road_surface_hazard():
    am = AlertManager()
    p = {"prediction": "normal", "confidence": 0.9, "fatigue_score": 0.2, "extra": {"asleep": False}}
    # Slippery at high speed -> critical skid risk
    s_slip = {"speed_kmh": 120, "road_surface": "slippery", "weather": "clear"}
    alerts = am.evaluate(p, s_slip)
    assert any(lvl == "critical" and "skid risk" in reason for lvl, reason in alerts)

    # Wet at moderate high speed -> warning
    s_wet = {"speed_kmh": 95, "road_surface": "wet", "weather": "clear"}
    alerts_wet = am.evaluate(p, s_wet)
    assert any(lvl == "warning" and "wet road" in reason for lvl, reason in alerts_wet)


def test_weather_and_light_hazards():
    am = AlertManager()
    p = {"prediction": "normal", "confidence": 0.9, "fatigue_score": 0.2, "extra": {"asleep": False}}
    # Fog -> warning
    alerts = am.evaluate(p, {"weather": "fog", "speed_kmh": 70})
    assert any(lvl == "warning" and "fog" in reason for lvl, reason in alerts)

    # Low light at high speed -> warning
    alerts_light = am.evaluate(p, {"light_lux": 20, "speed_kmh": 95})
    assert any(lvl == "warning" and "low ambient light" in reason for lvl, reason in alerts_light)


def test_low_confidence_ignored():
    assert AlertManager().evaluate({"prediction": "drowsy", "confidence": 0.3, "fatigue_score": 0.95}) == []
