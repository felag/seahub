define([
    'jquery',
    'underscore',
    'backbone',
    'common',
    'app/collections/share-admin-folders',
    'app/views/share-admin-folder'
], function($, _, Backbone, Common, ShareAdminFolderCollection, ShareAdminFolderView) {
    'use strict';

    var ShareAdminFoldersView = Backbone.View.extend({

        id: 'share-admin-folders',

        template: _.template($('#share-admin-folders-tmpl').html()),

        initialize: function() {
            this.folders = new ShareAdminFolderCollection();
            this.listenTo(this.folders, 'add', this.addOne);
            this.listenTo(this.folders, 'reset', this.reset);
            this.render();
        },

        events: {
            'click .by-name': 'sortByName'
        },

        sortByName: function() {
            var folders = this.folders;
            var el = $('.by-name .sort-icon', this.$table);
            folders.comparator = function(a, b) { // a, b: model
                var result = Common.compareTwoWord(a.get('folder_name'), b.get('folder_name'));
                if (el.hasClass('icon-caret-up')) {
                    return -result;
                } else {
                    return result;
                }
            };
            folders.sort();
            this.$tableBody.empty();
            folders.each(this.addOne, this);
            el.toggleClass('icon-caret-up icon-caret-down').show();
            folders.comparator = null;
            return false;
        },

        render: function() {
            this.$el.html(this.template());
            this.$table = this.$('table');
            this.$tableBody = $('tbody', this.$table);
            this.$loadingTip = this.$('.loading-tip');
            this.$emptyTip = this.$('.empty-tips');
        },

        hide: function() {
            this.$el.detach();
            this.attached = false;
        },

        show: function() {
            if (!this.attached) {
                this.attached = true;
                $("#right-panel").html(this.$el);
            }
            this.showLibraries();
        },

        showLibraries: function() {
            this.initPage();
            var _this = this;
            this.folders.fetch({
                cache: false, // for IE
                reset: true,
                error: function (xhr) {
                    Common.ajaxErrorHandler(xhr);
                }
            });
        },

        initPage: function() {
            this.$table.hide();
            this.$tableBody.empty();
            this.$loadingTip.show();
            this.$emptyTip.hide();
        },

        reset: function() {
            this.$('.error').hide();
            this.$loadingTip.hide();
            if (this.folders.length) {
                this.$emptyTip.hide();
                this.$tableBody.empty();
                this.folders.each(this.addOne, this);
                this.$table.show();
            } else {
                this.$table.hide();
                this.$emptyTip.show();
            }
        },

        addOne: function(folder) {
            var view = new ShareAdminFolderView({model: folder});
            this.$tableBody.append(view.render().el);
        }

    });

    return ShareAdminFoldersView;
});