#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Created by Hai-Tao Yu | 18/10/31 | https://y-research.github.io

"""Description

"""


from org.archive.ltr_adversarial_learning.base.ad_player import AdversarialPlayer

class Point_Generator(AdversarialPlayer):
    '''
    A pointwise generator
    '''
    def __init__(self, sf_para_dict=None):
        super(Point_Generator, self).__init__(sf_para_dict=sf_para_dict)