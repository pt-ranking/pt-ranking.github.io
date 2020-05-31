#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Created by Hai-Tao Yu | 18/11/21 | https://y-research.github.io

"""Description
The implementation builds upon the following libraries:
XGBoost:  https://github.com/dmlc/xgboost
LightGBM: https://github.com/microsoft/LightGBM
"""

import os
import sys
import pickle
import datetime
import numpy as np
from itertools import product
from sklearn.datasets import load_svmlight_file

import torch

import lightgbm as lgb
from lightgbm import Dataset

import xgboost as xgb
#from xgboost import DMatrix
from xgboost import XGBRanker

from org.archive.metric.adhoc_metric import torch_nDCG_at_ks, torch_nerr_at_ks, torch_ap_at_ks, torch_p_at_ks
from org.archive.data import data_utils
from org.archive.data.data_utils import prepare_data_for_lambdaMART, YAHOO_L2R

from org.archive.ltr_adhoc.listwise.lambdaMART.custom_lightgbm import lightgbm_custom_obj_ranknet, lightgbm_test_custom_obj, lightgbm_custom_obj_lambdarank, custom_eval_ndcg, lightgbm_custom_obj_ranknet_sharp, lightgbm_custom_obj_listnet

from org.archive.ltr_adhoc.listwise.lambdaMART.custom_xgboost import GDMatrix
from org.archive.ltr_adhoc.listwise.lambdaMART.custom_xgboost import xgboost_logregobj, xgboost_custom_obj_ranknet, xgboost_custom_obj_ranknet_sharp, xgboost_custom_obj_lambdarank, xgboost_custom_obj_listnet

from org.archive.utils.bigdata.BigPickle import pickle_save, pickle_load

class LambdaMARTEvaluator():
    """
    The class for evaluating LambdaMART building upon different Gradient Boosting Engines.
    """

    def __init__(self, engine='LightGBM'):
        self.engine = engine

    def load_group_data(self, file_group, use_np=False):
        """  """
        if use_np:
            return np.loadtxt(file_group)
        else:
            group = []
            with open(file_group, "r") as f:
                data = f.readlines()
                for line in data:
                    group.append(int(line.split("\n")[0]))
            return group


    def update_output_setting(self, para_dict=None, eval_dict=None):
        data_id, model, do_validation, root_output = eval_dict['data_id'], eval_dict['model'], eval_dict['do_validation'], eval_dict['dir_output']
        grid_search, min_docs, min_rele = eval_dict['grid_search'], eval_dict['min_docs'], eval_dict['min_rele']
        eta, gamma, min_child_weight, max_depth, tree_method = para_dict['eta'], para_dict['gamma'], para_dict['min_child_weight'], para_dict['max_depth'], para_dict['tree_method']

        lm_para_str = '_'.join(['{:,g}'.format(eta), '{:,g}'.format(gamma), '{:,g}'.format(min_child_weight), '{:,g}'.format(max_depth), tree_method])

        print(' '.join(['Start {} on {} for ltr_adhoc >>>'.format(model, data_id)]))

        if grid_search:
            root_output = root_output + '_'.join(['grid', model]) + '/'
            if not os.path.exists(root_output):
                os.makedirs(root_output)

        para_setting_str = '_'.join(['Vd', str(do_validation), 'Md', str(min_docs), 'Mr', str(min_rele), lm_para_str])
        file_prefix = '_'.join([model, data_id, para_setting_str])

        if eval_dict is not None and eval_dict['semi_context']:
            mask_ratio = eval_dict['mask_ratio']
            semi_ratio_str = '_'.join(['Semi', 'Ratio', '{:,g}'.format(mask_ratio)])
            file_prefix = '_'.join([file_prefix, semi_ratio_str])

        model_output = root_output + file_prefix + '/'  # model-specific outputs

        if not os.path.exists(model_output):
            os.makedirs(model_output)
        return model_output


    def result_to_str(self, list_scores=None, list_cutoffs=None, split_str=', ', metric_str=None):
        list_str = []
        for i in range(len(list_scores)):
            list_str.append('{}@{}:{:.4f}'.format(metric_str, list_cutoffs[i], list_scores[i]))
        return split_str.join(list_str)


    def cal_metric_at_ks(self, model, all_std_labels=None, all_preds=None, group=None, ks=[1, 3, 5, 10]):

        cnt = torch.zeros(1)

        sum_ndcg_at_ks = torch.zeros(len(ks))
        sum_nerr_at_ks = torch.zeros(len(ks))
        sum_ap_at_ks = torch.zeros(len(ks))
        sum_p_at_ks = torch.zeros(len(ks))

        list_ndcg_at_ks_per_q = []
        list_err_at_ks_per_q = []
        list_ap_at_ks_per_q = []
        list_p_at_ks_per_q = []

        tor_all_std_labels, tor_all_preds = torch.from_numpy(all_std_labels.astype(np.float32)), torch.from_numpy(all_preds.astype(np.float32))
        #tor_all_std_labels, tor_all_preds = tor_all_std_labels.double(), tor_all_preds.double()
        #print(tor_all_std_labels)
        #print(tor_all_preds)

        head = 0
        if model.endswith('LightGBM'): group = group.astype(np.int).tolist()
        for gr in group:
            tor_per_query_std_labels = tor_all_std_labels[head:head+gr]
            tor_per_query_preds = tor_all_preds[head:head+gr]
            head += gr

            _, tor_sorted_inds = torch.sort(tor_per_query_preds, descending=True)

            sys_sorted_labels = tor_per_query_std_labels[tor_sorted_inds]
            ideal_sorted_labels, _ = torch.sort(tor_per_query_std_labels, descending=True)
            #print(ideal_sorted_labels)

            ndcg_at_ks = torch_nDCG_at_ks(sys_sorted_labels=sys_sorted_labels, ideal_sorted_labels=ideal_sorted_labels, ks=ks, multi_level_rele=True)
            list_ndcg_at_ks_per_q.append(ndcg_at_ks.numpy())

            nerr_at_ks = torch_nerr_at_ks(sys_sorted_labels=sys_sorted_labels, ideal_sorted_labels=ideal_sorted_labels, ks=ks, multi_level_rele=True)
            list_err_at_ks_per_q.append(nerr_at_ks.numpy())

            ap_at_ks = torch_ap_at_ks(sys_sorted_labels=sys_sorted_labels, ideal_sorted_labels=ideal_sorted_labels, ks=ks)
            list_ap_at_ks_per_q.append(ap_at_ks.numpy())

            p_at_ks = torch_p_at_ks(sys_sorted_labels=sys_sorted_labels, ks=ks)
            list_p_at_ks_per_q.append(p_at_ks.numpy())

            sum_ndcg_at_ks = torch.add(sum_ndcg_at_ks, ndcg_at_ks)
            sum_nerr_at_ks = torch.add(sum_nerr_at_ks, nerr_at_ks)
            sum_ap_at_ks   = torch.add(sum_ap_at_ks, ap_at_ks)
            sum_p_at_ks    = torch.add(sum_p_at_ks, p_at_ks)
            cnt += 1

        tor_avg_ndcg_at_ks = sum_ndcg_at_ks / cnt
        avg_ndcg_at_ks = tor_avg_ndcg_at_ks.data.numpy()

        tor_avg_nerr_at_ks = sum_nerr_at_ks / cnt
        avg_nerr_at_ks = tor_avg_nerr_at_ks.data.numpy()

        tor_avg_ap_at_ks = sum_ap_at_ks / cnt
        avg_ap_at_ks = tor_avg_ap_at_ks.data.numpy()

        tor_avg_p_at_ks = sum_p_at_ks / cnt
        avg_p_at_ks = tor_avg_p_at_ks.data.numpy()

        return avg_ndcg_at_ks, avg_nerr_at_ks, avg_ap_at_ks, avg_p_at_ks, list_ndcg_at_ks_per_q, list_err_at_ks_per_q, list_ap_at_ks_per_q, list_p_at_ks_per_q


    def get_paras_XGBoost(self, para_dict=None):
        debug, grid_search = para_dict['debug'], para_dict['grid_search']

        if debug:
            params = {'objective': 'rank:ndcg', 'eta': 0.1, 'eval_metric':'ndcg@10'}

        elif grid_search:
            eta, gamma, min_child_weight, max_depth, tree_method = para_dict['eta'], para_dict['gamma'], para_dict[
                'min_child_weight'], para_dict['max_depth'], para_dict['tree_method']
            params = {'objective': 'rank:ndcg', 'eta': eta, 'gamma': gamma, 'min_child_weight': min_child_weight,
                      'max_depth': max_depth, 'eval_metric': 'ndcg@10-',
                      'tree_method': tree_method}  # if idealDCG=0, then 0

        else:
            #### rank:pairwise
            # params = {'objective': 'rank:pairwise', 'eta': 0.1, 'gamma': 1.0, 'min_child_weight': 0.1, 'max_depth': 6}

            # map
            # params = {'objective': 'rank:map', 'eta': 0.1, 'gamma': 1.0, 'min_child_weight': 0.1, 'max_depth': 6}

            #### ndcg
            # params = {'objective': 'rank:ndcg', 'eta': 0.1, 'gamma': 1.0, 'min_child_weight': 0.1, 'max_depth': 6}
            # params = {'objective': 'rank:ndcg', 'eta': 0.1, 'gamma': 1.0, 'min_child_weight': 0.1, 'max_depth': 6, 'eval_metric': 'ndcg@10'}

            #### According to LightGBM experiments

            # exact greedy
            #params = {'objective': 'rank:ndcg', 'eta': 0.1, 'max_depth': 8, 'max_round':500,  'nthread':16, 'tree_method':'exact', 'min_child_weight':100, 'eval_metric': 'ndcg@10'}

            # using histogram based algorithm
            params = {'objective': 'rank:ndcg',
                      'eta': 0.3,
                      'gamma': 0.,
                      'max_depth': 8,
                      'max_round': 500,
                      'nthread': 1,
                      'max_depth': 0, 'max_leaves': 255,
                      'tree_method':'hist', # auto, exact, approx, hist, gpu_hist
                      'grow_policy':'lossguide', # depthwise, lossguide
                      'min_child_weight': 5,
                      'eval_metric': 'ndcg@10'}


        return params


    def get_paras_LightGBM(self, para_dict=None):
        debug, grid_search = para_dict['debug'], para_dict['grid_search']

        if debug:
            params = {'learning_rate': 0.1}

        elif grid_search:
            raise NotImplementedError

        else: # default setting according to LightGBM experiments
            '''
            learning_rate = 0.1
            num_leaves = 255
            num_trees = 500
            num_threads = 16
            min_data_in_leaf = 0
            min_sum_hessian_in_leaf = 100
            '''
            params = {'learning_rate': 0.1, 'num_leaves':255, 'num_trees':500, 'num_threads':16, 'min_data_in_leaf':0, 'min_sum_hessian_in_leaf':100}

        return params


    def _fold_eval_lambdaMART_upon_LightGBM(self, fold_k, file_train, file_vali, file_test, para_dict=None, save_dir=None):

        min_docs, min_rele = para_dict['min_docs'], para_dict['min_rele']
        do_validation, validation_k = para_dict['do_validation'], para_dict['validation_k']

        file_train_data, file_train_group = prepare_data_for_lambdaMART(file_train, min_docs=min_docs, min_rele=min_rele, data_id=data_id)
        file_vali_data, file_vali_group   = prepare_data_for_lambdaMART(file_vali, min_docs=min_docs, min_rele=min_rele, data_id=data_id)
        file_test_data, file_test_group   = prepare_data_for_lambdaMART(file_test, min_docs=min_docs, min_rele=min_rele, data_id=data_id)

        x_train, y_train = load_svmlight_file(file_train_data)
        group_train = np.loadtxt(file_train_group)

        if do_validation:
            x_valid, y_valid = load_svmlight_file(file_vali_data)
            group_valid = np.loadtxt(file_vali_group)

        x_test, y_test = load_svmlight_file(file_test_data)
        group_test = np.loadtxt(file_test_group)

        gbm = lgb.LGBMRanker()
        params = self.get_paras_LightGBM(para_dict=para_dict)
        gbm.set_params(**params)

        obj_dict = dict(objective=lightgbm_test_custom_obj)
        gbm.set_params(**obj_dict)

        if do_validation:
            gbm.fit(x_train, y_train, group=group_train,
                    eval_set=[(x_valid, y_valid)], eval_group=[group_valid], eval_at=[validation_k], verbose=10)
        else:
            raise NotImplementedError

        if data_id in YAHOO_L2R:
            model_file = save_dir+'model.txt'
        else:
            model_file = save_dir+'_'.join(['fold', str(fold_k), 'model'])+'.txt'

        gbm.booster_.save_model(model_file)

        y_pred = gbm.predict(x_test)  # fold-wise performance

        return y_test, group_test, y_pred


    def fold_eval_lambdaMART_upon_LightGBM(self, fold_k, file_train, file_vali, file_test, para_dict=None, save_dir=None, grid=False, eval_dict=None):

        min_docs, min_rele = eval_dict['min_docs'], eval_dict['min_rele']
        do_validation, validation_k = eval_dict['do_validation'], eval_dict['validation_k']

        if eval_dict is not None and eval_dict['semi_context']:
            file_train_data, file_train_group = prepare_data_for_lambdaMART(file_train, train=True, min_docs=min_docs, min_rele=min_rele, data_id=data_id, eval_dict=eval_dict)
        else:
            file_train_data, file_train_group = prepare_data_for_lambdaMART(file_train, train=True, min_docs=min_docs, min_rele=min_rele, data_id=data_id)

        file_vali_data, file_vali_group   = prepare_data_for_lambdaMART(file_vali, min_docs=min_docs, min_rele=min_rele, data_id=data_id)
        file_test_data, file_test_group   = prepare_data_for_lambdaMART(file_test, min_docs=min_docs, min_rele=min_rele, data_id=data_id)

        x_train, y_train = load_svmlight_file(file_train_data)
        group_train = np.loadtxt(file_train_group)
        train_set = Dataset(data=x_train, label=y_train, group=group_train)

        if do_validation:
            x_valid, y_valid = load_svmlight_file(file_vali_data)
            group_valid = np.loadtxt(file_vali_group)
            valid_set = Dataset(data=x_valid, label=y_valid, group=group_valid)

        x_test, y_test = load_svmlight_file(file_test_data)
        group_test = np.loadtxt(file_test_group)
        test_set = Dataset(data=x_test, label=y_test, group=group_test)

        if grid:
            # needs a call like params = self.get_paras_LightGBM(para_dict=para_dict)
            # gbm.set_params(**params)
            raise NotImplementedError
        else:
            if eval_dict['plot']: # in order to plot the variation of performance w.r.t. train, validation, test
                eval_results = {}
                params = {'boosting_type': 'gbdt',  # ltr_gbdt, dart
                          'objective': 'lambdarank',
                          'metric': 'ndcg',
                          'learning_rate': 0.05,
                          'num_leaves': 400,
                          'num_trees': 1000,
                          'num_threads': 16,
                          'min_data_in_leaf': 50,
                          'min_sum_hessian_in_leaf': 200,
                          #'lambdamart_norm':False
                          }
                assert do_validation is True
                gbm = lgb.train(params=params, train_set=train_set,
                                valid_sets=[train_set, valid_set, test_set], valid_names=['Train', 'Valid', 'Test'],
                                verbose_eval=True, evals_result=eval_results, early_stopping_rounds=200)
            else:
                params = {'boosting_type': 'gbdt',  # ltr_gbdt, dart
                          'objective': 'lambdarank',
                          'metric': 'ndcg',
                          'learning_rate': 0.05,
                          'num_leaves': 400,
                          'num_trees': 1000,
                          'num_threads': 16,
                          'min_data_in_leaf': 50,
                          'min_sum_hessian_in_leaf': 200,
                          #'lambdamart_norm':False,
                          #'is_training_metric':True,
                          'verbosity': -1}

                if do_validation:
                    gbm = lgb.train(params=params, train_set=train_set, valid_sets=[valid_set], verbose_eval=10, early_stopping_rounds=100)
                else:
                    gbm = lgb.train(params=params, train_set=train_set, verbose_eval=10, num_boost_round=100)


        if data_id in YAHOO_L2R:
            model_file = save_dir+'model.txt'
        else:
            model_file = save_dir+'_'.join(['fold', str(fold_k), 'model'])+'.txt'

            if eval_dict['plot']: pickle_save(file=save_dir+'eval_results.dict', target=eval_results)

        gbm.save_model(model_file)

        y_pred = gbm.predict(x_test)  # fold-wise prediction

        return y_test, group_test, y_pred


    def check_group(self, train_dmatrix, group, y_train):
        labels = train_dmatrix.get_label()

        head = 0
        for gr in group:
            #print('From labels:\t', labels[head:head + gr])
            #print()
            #print('From input:\t', y_train[head:head + gr])
            #print('Np cmp:\t', np.equal(labels[head:head + gr], y_train[head:head + gr]))
            #print('Comparison:\t', (labels[head:head + gr] == y_train[head:head + gr]).all())
            if not (labels[head:head + gr] == y_train[head:head + gr]).all():
                print('Flase')
            #print()


            head += gr


    def _fold_eval_lambdaMART_upon_XGBoost(self, fold_k, file_train, file_vali, file_test, para_dict=None, save_dir=None):
        '''  '''
        min_docs, min_rele = para_dict['min_docs'], para_dict['min_rele']
        do_validation, validation_k = para_dict['do_validation'], para_dict['validation_k']

        file_train_data, file_train_group = prepare_data_for_lambdaMART(file_train, min_docs=min_docs, min_rele=min_rele, data_id=data_id)
        file_vali_data, file_vali_group   = prepare_data_for_lambdaMART(file_vali, min_docs=min_docs, min_rele=min_rele, data_id=data_id)
        file_test_data, file_test_group   = prepare_data_for_lambdaMART(file_test, min_docs=min_docs, min_rele=min_rele, data_id=data_id)

        x_train, y_train = load_svmlight_file(file_train_data)
        group_train = self.load_group_data(file_train_group)
        #train_dmatrix = DMatrix(x_train, y_train)
        train_dmatrix = GDMatrix(x_train, y_train, group=group_train)
        train_dmatrix.set_group(group_train)

        #self.check_group(train_dmatrix=train_dmatrix, group=group_train, y_train=y_train)

        if do_validation:
            x_valid, y_valid = load_svmlight_file(file_vali_data)
            group_valid = self.load_group_data(file_vali_group)
            valid_dmatrix = GDMatrix(x_valid, y_valid, group=group_valid)
            valid_dmatrix.set_group(group_valid)

        x_test, y_test = load_svmlight_file(file_test_data)
        group_test = self.load_group_data(file_test_group)
        test_dmatrix = GDMatrix(x_test, group=group_test)

        params = self.get_paras_XGBoost(para_dict=para_dict)
        if do_validation:
            fold_xgb_model = xgb.train(params, train_dmatrix,
                                       num_boost_round = 50,
                                       early_stopping_rounds=20,
                                       maximize=True,
                                       #learning_rates=0.001,
                                       evals=[(valid_dmatrix, 'validation')], verbose_eval=20,
                                       #obj=logregobj,
                                       #obj=xgboost_custom_obj_ranknet,
                                       #obj=xgboost_custom_obj_listnet,
                                       obj=xgboost_custom_obj_lambdarank
                                       )
        else:
            fold_xgb_model = xgb.train(params, train_dmatrix, verbose_eval=10)

        if data_id in YAHOO_L2R:
            with open(save_dir + 'model.dat', 'wb') as model_file:
                pickle.dump(fold_xgb_model, model_file)
        else:
            with open(save_dir + '_'.join(['fold', str(fold_k), 'model']) + '.dat', 'wb') as model_file:
                pickle.dump(fold_xgb_model, model_file)

        '''
        If early stopping occurs, the model will have three additional fields: bst.best_score, bst.best_iteration and bst.best_ntree_limit. Note that xgboost.train() will return a model from the last iteration, not the best one.
        '''

        print(fold_xgb_model)

        y_pred = fold_xgb_model.predict(test_dmatrix, ntree_limit=fold_xgb_model.best_iteration)  # fold-wise performance

        return y_test, group_test, y_pred

    def fold_eval_lambdaMART_upon_XGBoost(self, fold_k, file_train, file_vali, file_test, para_dict=None, save_dir=None):
        '''  '''
        min_docs, min_rele = para_dict['min_docs'], para_dict['min_rele']
        do_validation, validation_k = para_dict['do_validation'], para_dict['validation_k']

        file_train_data, file_train_group = prepare_data_for_lambdaMART(file_train, min_docs=min_docs, min_rele=min_rele, data_id=data_id)
        file_vali_data, file_vali_group   = prepare_data_for_lambdaMART(file_vali, min_docs=min_docs, min_rele=min_rele, data_id=data_id)
        file_test_data, file_test_group   = prepare_data_for_lambdaMART(file_test, min_docs=min_docs, min_rele=min_rele, data_id=data_id)

        x_train, y_train = load_svmlight_file(file_train_data)
        group_train = self.load_group_data(file_train_group)
        #train_dmatrix = DMatrix(x_train, y_train)
        train_dmatrix = GDMatrix(x_train, y_train, group=group_train)
        train_dmatrix.set_group(group_train)

        #self.check_group(train_dmatrix=train_dmatrix, group=group_train, y_train=y_train)

        if do_validation:
            x_valid, y_valid = load_svmlight_file(file_vali_data)
            group_valid = self.load_group_data(file_vali_group)
            valid_dmatrix = GDMatrix(x_valid, y_valid, group=group_valid)
            valid_dmatrix.set_group(group_valid)

        x_test, y_test = load_svmlight_file(file_test_data)
        group_test = self.load_group_data(file_test_group)
        test_dmatrix = GDMatrix(x_test, group=group_test)

        params = self.get_paras_XGBoost(para_dict=para_dict)
        if do_validation:
            fold_xgb_model = xgb.train(params, train_dmatrix,
                                       num_boost_round = 50,
                                       early_stopping_rounds=20,
                                       maximize=True,
                                       #learning_rates=0.001,
                                       evals=[(valid_dmatrix, 'validation')], verbose_eval=20,
                                       #obj=logregobj,
                                       #obj=xgboost_custom_obj_ranknet,
                                       #obj=xgboost_custom_obj_listnet,
                                       obj=xgboost_custom_obj_lambdarank
                                       )
        else:
            fold_xgb_model = xgb.train(params, train_dmatrix, verbose_eval=10)

        if data_id in YAHOO_L2R:
            with open(save_dir + 'model.dat', 'wb') as model_file:
                pickle.dump(fold_xgb_model, model_file)
        else:
            with open(save_dir + '_'.join(['fold', str(fold_k), 'model']) + '.dat', 'wb') as model_file:
                pickle.dump(fold_xgb_model, model_file)

        '''
        If early stopping occurs, the model will have three additional fields: bst.best_score, bst.best_iteration and bst.best_ntree_limit. Note that xgboost.train() will return a model from the last iteration, not the best one.
        '''

        print(fold_xgb_model)

        y_pred = fold_xgb_model.predict(test_dmatrix, ntree_limit=fold_xgb_model.best_iteration)  # fold-wise performance

        return y_test, group_test, y_pred


    def cv_eval_lambdaMART(self, para_dict=None, eval_dict=None):
        ''' Evaluation based on k-fold cross validation if multiple folds exist '''

        debug, data_id, dir_data, model = eval_dict['debug'], eval_dict['data_id'], eval_dict['dir_data'], eval_dict['model']
        cutoffs, do_validation, do_log = eval_dict['cutoffs'], eval_dict['do_validation'], eval_dict['do_log']

        fold_num = 2 if debug else 5
        if data_id in YAHOO_L2R: fold_num = 1
        if eval_dict['plot']: fold_num = 1

        model_output = self.update_output_setting(eval_dict=eval_dict, para_dict=para_dict)
        if do_log: sys.stdout = open(model_output + 'log.txt', "w")

        time_begin = datetime.datetime.now()        # timing
        l2r_cv_avg_ndcg_scores = np.zeros(len(cutoffs))  # fold average
        l2r_cv_avg_nerr_scores = np.zeros(len(cutoffs))  # fold average
        l2r_cv_avg_ap_scores = np.zeros(len(cutoffs))  # fold average
        l2r_cv_avg_p_scores = np.zeros(len(cutoffs))  # fold average

        list_all_fold_ndcg_at_ks_per_q = []
        list_all_fold_err_at_ks_per_q = []
        list_all_fold_ap_at_ks_per_q = []
        list_all_fold_p_at_ks_per_q = []

        for fold_k in range(1, fold_num + 1):
            if data_id in YAHOO_L2R:
                data_prefix = dir_data + data_id.lower() + '.'
                ori_file_train, ori_file_vali, ori_file_test = data_prefix + 'train.txt', data_prefix + 'valid.txt', data_prefix + 'test.txt'
            else:
                print('\nFold-', fold_k)            # fold-wise data preparation plus certain light filtering
                dir_fold_k = dir_data + 'Fold' + str(fold_k) + '/'
                ori_file_train, ori_file_vali, ori_file_test = dir_fold_k + 'train.txt', dir_fold_k + 'vali.txt', dir_fold_k + 'test.txt'

            if data_id in YAHOO_L2R:
                save_dir = model_output
            else:
                fold_checkpoint = '-'.join(['Fold', str(fold_k)])   # buffer model
                save_dir = model_output + fold_checkpoint + '/'

            if not os.path.exists(save_dir): os.makedirs(save_dir)

            if model.endswith('XGBoost'):
                y_test, group_test, y_pred = self.fold_eval_lambdaMART_upon_XGBoost(fold_k=fold_k, file_train=ori_file_train, file_vali=ori_file_vali, file_test=ori_file_test,
                                                                                    para_dict=para_dict, save_dir=save_dir)
            elif model.endswith('LightGBM'):
                y_test, group_test, y_pred = self.fold_eval_lambdaMART_upon_LightGBM(fold_k=fold_k, file_train=ori_file_train, file_vali=ori_file_vali, file_test=ori_file_test,
                                                                                    para_dict=para_dict, save_dir=save_dir, eval_dict=eval_dict)
            else:
                raise NotImplementedError

            fold_avg_ndcg_at_ks, fold_avg_nerr_at_ks, fold_avg_ap_at_ks, fold_avg_p_at_ks,\
            list_ndcg_at_ks_per_q, list_err_at_ks_per_q, list_ap_at_ks_per_q, list_p_at_ks_per_q = self.cal_metric_at_ks(model=model, all_std_labels=y_test, all_preds=y_pred, group=group_test, ks=cutoffs)

            performance_list = [model] if data_id in YAHOO_L2R else [model + ' Fold-' + str(fold_k)]

            for i, co in enumerate(cutoffs):
                performance_list.append('\nnDCG@{}:{:.4f}'.format(co, fold_avg_ndcg_at_ks[i]))
            for i, co in enumerate(cutoffs):
                performance_list.append('\nnERR@{}:{:.4f}'.format(co, fold_avg_nerr_at_ks[i]))
            for i, co in enumerate(cutoffs):
                performance_list.append('\nMAP@{}:{:.4f}'.format(co, fold_avg_ap_at_ks[i]))
            for i, co in enumerate(cutoffs):
                performance_list.append('\nP@{}:{:.4f}'.format(co, fold_avg_p_at_ks[i]))

            performance_str = '\t'.join(performance_list)
            print('\n\t', performance_str)

            l2r_cv_avg_ndcg_scores = np.add(l2r_cv_avg_ndcg_scores, fold_avg_ndcg_at_ks)  # sum for later cv-performance
            l2r_cv_avg_nerr_scores = np.add(l2r_cv_avg_nerr_scores, fold_avg_nerr_at_ks)  # sum for later cv-performance
            l2r_cv_avg_ap_scores   = np.add(l2r_cv_avg_ap_scores, fold_avg_ap_at_ks)  # sum for later cv-performance
            l2r_cv_avg_p_scores    = np.add(l2r_cv_avg_p_scores, fold_avg_p_at_ks)  # sum for later cv-performance

            list_all_fold_ndcg_at_ks_per_q.extend(list_ndcg_at_ks_per_q)
            list_all_fold_err_at_ks_per_q.extend(list_err_at_ks_per_q)
            list_all_fold_ap_at_ks_per_q.extend(list_ap_at_ks_per_q)
            list_all_fold_p_at_ks_per_q.extend(list_p_at_ks_per_q)

        time_end = datetime.datetime.now()  # overall timing
        elapsed_time_str = str(time_end - time_begin)
        print('Elapsed time:\t', elapsed_time_str + "\n")

        print()  # begin to print either cv or average performance
        l2r_cv_avg_ndcg_scores = np.divide(l2r_cv_avg_ndcg_scores, fold_num)
        l2r_cv_avg_nerr_scores = np.divide(l2r_cv_avg_nerr_scores, fold_num)
        l2r_cv_avg_ap_scores   = np.divide(l2r_cv_avg_ap_scores, fold_num)
        l2r_cv_avg_p_scores    = np.divide(l2r_cv_avg_p_scores, fold_num)

        if do_validation:
            eval_prefix = str(fold_num)+'-fold cross validation scores:'
        else:
            eval_prefix = str(fold_num) + '-fold average scores:'

        print(model, eval_prefix, self.result_to_str(list_scores=l2r_cv_avg_ndcg_scores, list_cutoffs=cutoffs, metric_str='nDCG'))
        print(model, eval_prefix, self.result_to_str(list_scores=l2r_cv_avg_nerr_scores, list_cutoffs=cutoffs, metric_str='nERR'))
        print(model, eval_prefix, self.result_to_str(list_scores=l2r_cv_avg_ap_scores, list_cutoffs=cutoffs, metric_str='MAP'))
        print(model, eval_prefix, self.result_to_str(list_scores=l2r_cv_avg_p_scores, list_cutoffs=cutoffs, metric_str='P'))

        all_fold_ndcg_at_ks_per_q = np.vstack(list_all_fold_ndcg_at_ks_per_q)
        all_fold_err_at_ks_per_q = np.vstack(list_all_fold_err_at_ks_per_q)
        all_fold_ap_at_ks_per_q = np.vstack(list_all_fold_ap_at_ks_per_q)
        all_fold_p_at_ks_per_q = np.vstack(list_all_fold_p_at_ks_per_q)

        pickle_save(all_fold_ndcg_at_ks_per_q, file=model_output + '_'.join([data_id, model, 'all_fold_ndcg_at_ks_per_q.np']))
        pickle_save(all_fold_err_at_ks_per_q, file=model_output + '_'.join([data_id, model, 'all_fold_err_at_ks_per_q.np']))
        pickle_save(all_fold_ap_at_ks_per_q, file=model_output + '_'.join([data_id, model, 'all_fold_ap_at_ks_per_q.np']))
        pickle_save(all_fold_p_at_ks_per_q, file=model_output + '_'.join([data_id, model, 'all_fold_p_at_ks_per_q.np']))

        return l2r_cv_avg_ndcg_scores, l2r_cv_avg_nerr_scores, l2r_cv_avg_ap_scores, l2r_cv_avg_p_scores


    def log_max(self, dir_output=None, max_cv_avg_scores=None, para_dict=None, cutoffs=None, dataset=None):
        model = para_dict['model']
        with open(file=dir_output + '_'.join(['grid', model]) + '/' + dataset + '_max.txt', mode='w') as max_writer:
            eta, gamma, min_child_weight, max_depth, tree_method = para_dict['eta'], para_dict['gamma'], para_dict['min_child_weight'], para_dict['max_depth'], para_dict['tree_method']
            para_str = '\n'.join(['eta: '+'{:,g}'.format(eta), 'gamma: '+'{:,g}'.format(gamma), 'min_child_weight: '+'{:,g}'.format(min_child_weight), 'max_depth: '+'{:,g}'.format(max_depth), 'tree_method: '+tree_method])
            max_writer.write(para_str + '\n')
            max_writer.write(self.result_to_str(max_cv_avg_scores, cutoffs))


    def grid_run_lambdaMART(self, data_id=None, dir_data=None, dir_output=None, debug=False):
        do_log = False if debug else True

        ''' settings that are rarely changed '''
        validation_k = 10
        min_docs = 10
        cutoffs = [1, 3, 5, 10, 20, 50]

        ''' setting w.r.t. data-preprocess '''

        ''' setting w.r.t. train  '''
        choice_validation = [True]  # True, False

        """ setting w.r.t. LambdaMART """
        choice_eta = [0.1]   if debug else [0.1] # learning_rate, range: [0,1], step size shrinkage used in update to prevents overfitting
        choice_gamma = [0.1] if debug else [0.0] # range: [0,∞] Minimum loss reduction required to make a further partition on a leaf node of the tree. The larger gamma is, the more conservative the algorithm will be.

        choice_min_child_weight = [1.0] if debug else [100]
        # range: [0,∞] Minimum sum of instance weight (hessian) needed in a child. If the tree partition step results in a leaf node with the sum of instance weight less than min_child_weight,
        # then the building process will give up further partitioning. In linear regression task, this simply corresponds to minimum number of instances needed to be in each node.
        # The larger min_child_weight is, the more conservative the algorithm will be.

        choice_max_depth = [6]        if debug else  [8]  # 6, 12, 20 range: [0,∞] Maximum depth of a tree. Increasing this value will make the model more complex and more likely to overfit
        choice_tree_method = ['auto', 'exact'] if debug else ['auto', 'exact', 'hist']  # auto, exact

        max_cv_avg_scores = np.zeros(len(cutoffs))  # fold average
        k_index = cutoffs.index(validation_k)
        max_para_dict = None

        for vd in choice_validation:
            for eta, gamma, min_child_weight, max_depth, tree_method in product(choice_eta, choice_gamma, choice_min_child_weight, choice_max_depth, choice_tree_method):
                            para_dict = dict(grid_search=True, debug=debug, dataset=data_id, dir_data=dir_data, dir_output=dir_output,
                                             model='LambdaMART', min_docs=min_docs, min_rele=1, cutoffs=cutoffs,
                                             do_validation=vd, validation_k=validation_k, do_log=do_log,
                                             eta=eta, gamma=gamma, min_child_weight=min_child_weight, max_depth=max_depth, tree_method=tree_method)

                            curr_cv_avg_scores, _, _, _ = self.cv_eval_lambdaMART(para_dict=para_dict)
                            if curr_cv_avg_scores[k_index] > max_cv_avg_scores[k_index]:
                                max_cv_avg_scores, max_para_dict = curr_cv_avg_scores, para_dict

        #record optimal setting
        self.log_max(dir_output=dir_output, max_cv_avg_scores=max_cv_avg_scores, para_dict=max_para_dict, cutoffs=cutoffs, dataset=data_id)


    def point_run_lambdaMART(self, data_id=None, dir_data=None, dir_output=None, debug=False, eval_dict=None):
        #do_log = False if debug else True
        do_log = True
        plot = False

        eval_dict.update(dict(model='_'.join(['LambdaMART', self.engine]), cutoffs=[1, 3, 5, 10, 20], min_docs=10, min_rele=1, validation_k=10, do_log=do_log, plot=plot))

        para_dict = dict(eta=0.05, gamma=0.0, min_child_weight=100, max_depth=8, tree_method='exact')

        self.cv_eval_lambdaMART(para_dict=para_dict, eval_dict=eval_dict)



############
# Testing
############

from org.archive.ltr_adhoc.eval.test_l2r_tao import get_in_out_dir



if __name__ == '__main__':

    """
    >>> Supported datasets <<<
    MQ2007_Super | MQ2008_Super | MQ2007_Semi | MQ2008_Semi | MSLRWEB10K | MSLRWEB30K | Set1 (Yahoo_L2R) | Set2 (Yahoo_L2R)
    """

    #data_id = 'MSLRWEB10K'
    data_id = 'MSLRWEB30K'
    #data_id = 'MQ2008_Super'

    dir_data, dir_output = get_in_out_dir(data_id, pc='mbox-f3')


    debug = False

    grid_search = False

    #engine = 'XGBoost'
    engine = 'LightGBM'

    evaluator = LambdaMARTEvaluator(engine=engine)

    '''
    {semi_context} is used to test the effect of partially masking ground-truth labels with a specified ratio
    currently, it only supports {MSLRWEB10K | MSLRWEB30K}
    '''
    semi_context = False
    if semi_context:
        assert not data_id in data_utils.MSLETOR_SEMI
        mask_ratio = 0.2
        mask_type  = 'rand_mask_rele'
    else:
        mask_ratio = None
        mask_type  = None

    if semi_context:
        do_validation = False
    else:
        do_validation = True

    # as a baseline for comparison w.r.t. adversarial methods
    do_validation = False

    eval_dict = dict(debug=debug, grid_search=grid_search, data_id=data_id, dir_data=dir_data, dir_output=dir_output,
                     semi_context=semi_context, mask_ratio=mask_ratio, mask_type=mask_type, do_validation=do_validation)

    if grid_search:
        evaluator.grid_run_lambdaMART(data_id=data_id, dir_data=dir_data, dir_output=dir_output, debug=debug)

    else:
        evaluator.point_run_lambdaMART(data_id=data_id, dir_data=dir_data, dir_output=dir_output, debug=debug, eval_dict=eval_dict)