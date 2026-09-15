package controller

import (
    "context"
    "fmt"
    "regexp"
    "strings"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    admissionv1 "k8s.io/api/admissionregistration/v1"
    corev1 "k8s.io/api/core/v1"
    nodev1 "k8s.io/api/node/v1"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    klabels "k8s.io/apimachinery/pkg/labels"
    "k8s.io/apimachinery/pkg/types"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
)

type ProfileReconciler struct{ Base }
var gpuUUIDPattern=regexp.MustCompile(`GPU-[0-9a-fA-F-]{36}`)
func (b *Base) profileReady(ctx context.Context,p *api.FlytGPUProfile) error {
    if !p.Spec.Approved{return fmt.Errorf("administrator profile approval is absent")}
    node:=&corev1.Node{}
    if err:=b.Reader.Get(ctx,types.NamespacedName{Name:p.Spec.NodeName},node);err!=nil{return err}
    if node.Spec.Unschedulable||node.Labels["infinitiessoft.com/gpu-share"]=="true"{return fmt.Errorf("node is cordoned or managed by the external GPU platform")}
    ready:=false;for _,c:=range node.Status.Conditions{if c.Type==corev1.NodeReady&&c.Status==corev1.ConditionTrue{ready=true}}
    if !ready{return fmt.Errorf("GPU node is not Ready")}
    ids:=gpuUUIDPattern.FindAllString(node.Annotations["hami.io/node-nvidia-register"],-1)
    if len(ids)!=1||ids[0]!=p.Spec.GPUUUID{return fmt.Errorf("HAMi must register exactly the approved GPU UUID")}
    if p.Spec.RuntimeClass!=""{if err:=b.Reader.Get(ctx,types.NamespacedName{Name:p.Spec.RuntimeClass},&nodev1.RuntimeClass{});err!=nil{return err}}
    pods:=&corev1.PodList{}
    if err:=b.Reader.List(ctx,pods,client.MatchingFields{"spec.nodeName":p.Spec.NodeName});err!=nil{return err}
    plugin:=false
    for _,pod:=range pods.Items{
        if pod.Status.Phase==corev1.PodSucceeded||pod.Status.Phase==corev1.PodFailed{continue}
        for _,v:=range pod.Spec.Volumes{if v.HostPath!=nil&&strings.Contains(v.HostPath.Path,"kubelet/device-plugins"){
            if pod.Namespace!=p.Spec.HAMiNamespace||pod.Labels["app.kubernetes.io/instance"]!=p.Spec.SchedulerName{return fmt.Errorf("foreign device plugin on selected node")}
            for _,c:=range pod.Status.Conditions{if c.Type==corev1.PodReady&&c.Status==corev1.ConditionTrue{plugin=true}}
        }}
        ours:=pod.Namespace==p.Namespace&&pod.Labels[Experiment]==Managed&&pod.Labels[WorkerLabel]!=""
        if len(pod.Spec.ResourceClaims)>0&&!ours{return fmt.Errorf("foreign device claim on selected node")}
        for _,c:=range append(pod.Spec.Containers,pod.Spec.InitContainers...){
            for name:=range c.Resources.Limits{if (strings.HasPrefix(string(name),"nvidia.com/")||strings.HasPrefix(string(name),"gpu-isolation/"))&&!ours{return fmt.Errorf("foreign GPU workload on selected node")}}
        }
    }
    if !plugin{return fmt.Errorf("existing HAMi device plugin is not Ready")}
    hooks:=&admissionv1.MutatingWebhookConfigurationList{}
    if err:=b.Reader.List(ctx,hooks);err!=nil{return err}
    scoped:=false
    for _,h:=range hooks.Items{
        if h.Annotations["meta.helm.sh/release-name"]!=p.Spec.SchedulerName||h.Annotations["meta.helm.sh/release-namespace"]!=p.Spec.HAMiNamespace{continue}
        for _,hook:=range h.Webhooks{
            if hook.NamespaceSelector==nil||hook.ObjectSelector==nil||hook.FailurePolicy==nil||*hook.FailurePolicy!=admissionv1.Fail{continue}
            ns,err:=metav1.LabelSelectorAsSelector(hook.NamespaceSelector);if err!=nil{continue}
            obj,err:=metav1.LabelSelectorAsSelector(hook.ObjectSelector);if err!=nil{continue}
            // Reject unconstrained selectors even if they happen to match.
            nsBound:=hook.NamespaceSelector.MatchLabels["kubernetes.io/metadata.name"]==p.Namespace
            for _,expr:=range hook.NamespaceSelector.MatchExpressions{if expr.Key=="kubernetes.io/metadata.name"&&expr.Operator==metav1.LabelSelectorOpIn{nsBound=true}}
            objBound:=hook.ObjectSelector.MatchLabels[Experiment]==Managed
            for _,expr:=range hook.ObjectSelector.MatchExpressions{if expr.Key==Experiment&&expr.Operator==metav1.LabelSelectorOpIn{objBound=true}}
            if nsBound&&objBound&&ns.Matches(klabels.Set{"kubernetes.io/metadata.name":p.Namespace})&&obj.Matches(klabels.Set{Experiment:Managed})&&
                hook.ClientConfig.Service!=nil&&hook.ClientConfig.Service.Namespace==p.Spec.HAMiNamespace{scoped=true}
        }
    }
    if !scoped{return fmt.Errorf("existing HAMi webhook does not explicitly include this stage-3 namespace and label")}
    return nil
}
func (r *ProfileReconciler) Reconcile(ctx context.Context,req ctrl.Request)(ctrl.Result,error){
    p:=&api.FlytGPUProfile{};if err:=r.Reader.Get(ctx,req.NamespacedName,p);err!=nil{return ctrl.Result{},client.IgnoreNotFound(err)}
    workers,err:=r.workers(ctx,p.Namespace);if err!=nil{return ctrl.Result{},err}
    count:=int32(0);for _,w:=range workers{if w.Spec.ProfileRef.Name==p.Name{count++}}
    if !p.DeletionTimestamp.IsZero(){
        if count>0{return r.mark(ctx,p,"Terminating","InUse","Delete referencing FlytWorkers before removing this profile")}
        _,err=r.finalizer(ctx,p,false);return ctrl.Result{},err
    }
    if changed,err:=r.finalizer(ctx,p,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    phase,reason,msg:="Ready","ApprovedAndRegistered","Approved profile and existing HAMi registration observed; GPU execution is unvalidated"
    if err=r.profileReady(ctx,p);err!=nil{phase,reason,msg="Blocked","PrerequisiteMissing",err.Error()}
    err=r.status(ctx,p,phase,reason,msg,func(s *api.Status){s.Consumers=count})
    return ctrl.Result{RequeueAfter:Period},err
}
