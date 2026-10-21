from scripts.evaluate import norm


def test_percent_in_prose_is_not_a_latex_comment():
    assert norm('77.2% 的消费者关注新闻。') == '772的消费者关注新闻'


def test_table_math_formats_are_comparable():
    assert norm(r'$1 = \text{女}$; $2 = \text{男}$') == norm('1=女;2=男')
    assert norm(r'$ {X}_{1} $') == norm('$X_1$')
